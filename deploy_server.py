#!/usr/bin/env python3
# =============================================================================
# Webhook independent of assistant.py
# Allows Claude to: read/write files, patch, restart
# =============================================================================

import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

_LOG_LOCK = threading.Lock()

CONFIG_PATH = "/home/lolufe/assistant/config.json"
SCRIPT_PATH = "/home/lolufe/assistant/assistant.py"
ASSISTANT_DIR = "/home/lolufe/assistant"
VERSIONS_DIR = "/home/lolufe/assistant/versions"
DEPLOY_LOG = "/home/lolufe/assistant/deploy.log"
PORT = 8501

# ═══ INFRA ═══
TUNNEL_URL_FILE = "/home/lolufe/assistant/tunnel_url.txt"
NTFY_TOPIC = "assistant-deploy-8501-secret"
HEARTBEAT_INTERVAL = 3600  # 1h — keeps the URL fresh within ntfy's 24h window

# Systemd unit files (written by /infra_install)
DEPLOY_SERVER_UNIT = """[Unit]
Description=AI Companion Deploy Server
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=lolufe
WorkingDirectory=/home/lolufe/assistant
ExecStart=/usr/bin/python3 -u /home/lolufe/assistant/deploy_server.py
Restart=always
RestartSec=3
StandardOutput=append:/home/lolufe/assistant/deploy_server.log
StandardError=append:/home/lolufe/assistant/deploy_server.log
KillMode=control-group
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
"""

WATCHDOG_SERVICE_UNIT = """[Unit]
Description=AI Companion Infra Watchdog (one-shot health check)

[Service]
Type=oneshot
User=lolufe
WorkingDirectory=/home/lolufe/assistant
ExecStart=/bin/bash /home/lolufe/assistant/watchdog.sh
StandardOutput=append:/home/lolufe/assistant/watchdog.log
StandardError=append:/home/lolufe/assistant/watchdog.log
"""

WATCHDOG_TIMER_UNIT = """[Unit]
Description=AI Companion Infra Watchdog timer (every 2 min)

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
Unit=infra_watchdog.service
AccuracySec=10s

[Install]
WantedBy=timers.target
"""

CLOUDFLARED_TUNNEL_UNIT = """[Unit]
Description=Cloudflare Tunnel for AI Companion Deploy Server
After=network-online.target deploy_server.service
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=lolufe
WorkingDirectory=/home/lolufe/assistant
ExecStart=/bin/bash /home/lolufe/assistant/tunnel_wrapper.sh
Restart=always
RestartSec=5
StandardOutput=append:/home/lolufe/assistant/tunnel.log
StandardError=append:/home/lolufe/assistant/tunnel.log
KillMode=control-group
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
"""


ALLOWED_EXTENSIONS = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".sh", ".cfg", ".ini", ".log", ".example", ".template", ".service", ".toml"}
# Files without extension allowed (Dockerfile, LICENSE, Makefile, etc.)
ALLOWED_NO_EXT = {"Dockerfile", "LICENSE", "Makefile", ".gitignore", ".dockerignore", "requirements"}
FORBIDDEN_PATHS = {"config.json"}

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

MAX_DEPLOY_LOG_KB = 500  # Rotate when log exceeds 500KB

def log_deploy(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    try:
        with _LOG_LOCK:
            with open(DEPLOY_LOG, "a") as f:
                f.write(line + "\n")
            # Rotate if too large
            if os.path.getsize(DEPLOY_LOG) > MAX_DEPLOY_LOG_KB * 1024:
                with open(DEPLOY_LOG, "r") as f:
                    lines = f.readlines()
                with open(DEPLOY_LOG, "w") as f:
                    f.writelines(lines[-1000:])  # Keep the last 1000 lines
    except Exception:
        pass

def verify_auth(headers, body=b""):
    cfg = load_config()
    secret = cfg.get("deploy_secret", "")
    if not secret:
        return False
    auth_header = headers.get("Authorization", "")
    if not auth_header.startswith("HMAC "):
        return False
    provided_hmac = auth_header[5:]
    expected_hmac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided_hmac, expected_hmac)

def verify_token_simple(headers):
    cfg = load_config()
    secret = cfg.get("deploy_secret", "")
    if not secret:
        return False
    auth_header = headers.get("Authorization", "")
    return auth_header == f"Bearer {secret}"

def _resolve_path(filepath):
    if not os.path.isabs(filepath):
        filepath = os.path.join(ASSISTANT_DIR, filepath)
    filepath = os.path.realpath(filepath)
    if not filepath.startswith(ASSISTANT_DIR):
        return None
    return filepath

def _extract_version(code):
    for line in code.split("\n"):
        if line.strip().startswith("VERSION") and "=" in line:
            parts = line.split("=", 1)
            return parts[1].strip().strip('"').strip("'")
    return "unknown"

def _security_checks(code):
    required = ["def main():", "def handle_message(", "def telegram_send(", "CFG = load_config()"]
    for r in required:
        if r.lower() not in code.lower():
            return f"Required element missing: {r}"
    forbidden = ["os.system('rm -rf", "shutil.rmtree('/'", "subprocess.run(['rm'"]
    for f in forbidden:
        if f in code:
            return f"Dangerous code: {f[:30]}"
    return None

# === ACTIONS ===

def action_read(filepath=None):
    if filepath is None:
        filepath = SCRIPT_PATH
    else:
        filepath = _resolve_path(filepath)
        if not filepath:
            return {"status": "error", "message": "Path not allowed"}
    try:
        with open(filepath, "r") as f:
            content = f.read()
        return {
            "status": "ok", "path": filepath,
            "lines": len(content.split("\n")),
            "size_kb": round(len(content) / 1024, 1),
            "content": content,
            "version": _extract_version(content) if filepath == SCRIPT_PATH else None,
            "timestamp": datetime.now().isoformat()
        }
    except FileNotFoundError:
        return {"status": "error", "message": f"File not found: {filepath}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def action_write_file(data):
    filepath = data.get("path", "")
    content = data.get("content", "")
    if not filepath or not content:
        return {"status": "error", "message": "path and content are required"}
    resolved = _resolve_path(filepath)
    if not resolved:
        return {"status": "error", "message": f"Path not allowed: {filepath}"}
    _, ext = os.path.splitext(resolved)
    basename_check = os.path.basename(resolved)
    if ext.lower() not in ALLOWED_EXTENSIONS and basename_check not in ALLOWED_NO_EXT:
        return {"status": "error", "message": f"Extension not allowed: {ext} (basename: {basename_check})"}
    basename = os.path.basename(resolved)
    if basename in FORBIDDEN_PATHS:
        return {"status": "error", "message": f"Protected file: {basename}"}
    try:
        if os.path.exists(resolved):
            backup_name = f"{basename}.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.copy2(resolved, os.path.join(VERSIONS_DIR, backup_name))
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        with open(resolved, "w") as f:
            f.write(content)
        log_deploy(f"✅ File written: {resolved} ({len(content.split(chr(10)))} lines)")
        return {"status": "ok", "path": resolved, "lines": len(content.split("\n")),
                "size_kb": round(len(content) / 1024, 1), "timestamp": datetime.now().isoformat()}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def action_run_v2_push():
    """Launches scripts/v2_push.sh detached."""
    script_path = os.path.join(ASSISTANT_DIR, "scripts", "v2_push.sh")
    if not os.path.exists(script_path):
        return {"status": "error", "message": "scripts/v2_push.sh missing"}
    subprocess.Popen(["bash", script_path], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"status": "ok", "message": "v2_push launched — wait 30s"}


def action_chmod(data):
    """chmod on a file (whitelist, +x only for security)."""
    filepath = data.get("path", "")
    mode = data.get("mode", "")
    if not filepath or not mode:
        return {"status": "error", "message": "path and mode are required"}
    if ".." in filepath or filepath.startswith("/"):
        return {"status": "error", "message": "Path not allowed"}
    full_path = os.path.join(ASSISTANT_DIR, filepath)
    if not os.path.exists(full_path):
        return {"status": "error", "message": f"Fichier introuvable: {filepath}"}
    if mode not in ["0o755", "755", "+x", "0o644", "644", "0o600", "600"]:
        return {"status": "error", "message": "Mode not allowed"}
    try:
        if mode in ["0o755", "755", "+x"]:
            os.chmod(full_path, 0o755)
        elif mode in ["0o644", "644"]:
            os.chmod(full_path, 0o644)
        elif mode in ["0o600", "600"]:
            os.chmod(full_path, 0o600)
        return {"status": "ok", "path": filepath, "mode": oct(os.stat(full_path).st_mode)[-3:]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def action_delete_file(data):
    """Deletes a file from the assistant folder (secured)"""
    filepath = data.get("path", "")
    if not filepath:
        return {"status": "error", "message": "path is required"}

    # Security: only within the assistant folder, no ..
    if ".." in filepath or filepath.startswith("/"):
        return {"status": "error", "message": "Path not allowed"}

    full_path = os.path.join(ASSISTANT_DIR, filepath)

    # Prevent deletion of critical files
    PROTEGE = {"assistant.py", "deploy_server.py", "config.json", "memory.db"}
    if os.path.basename(full_path) in PROTEGE:
        return {"status": "error", "message": f"Protected file: {filepath}"}

    if not os.path.exists(full_path):
        return {"status": "error", "message": f"File does not exist: {filepath}"}

    try:
        os.remove(full_path)
        log_deploy(f"File deleted: {filepath}")
        return {"status": "ok", "deleted": filepath, "timestamp": datetime.now().isoformat()}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def action_list_files(subdir=""):
    target = os.path.join(ASSISTANT_DIR, subdir) if subdir else ASSISTANT_DIR
    target = os.path.realpath(target)
    if not target.startswith(ASSISTANT_DIR):
        return {"status": "error", "message": "Path not allowed"}
    try:
        entries = []
        for item in sorted(os.listdir(target)):
            full = os.path.join(target, item)
            rel = os.path.relpath(full, ASSISTANT_DIR)
            if os.path.isdir(full):
                entries.append({"name": rel, "type": "dir"})
            else:
                entries.append({"name": rel, "type": "file", "size_kb": round(os.path.getsize(full) / 1024, 1)})
        return {"status": "ok", "path": os.path.relpath(target, ASSISTANT_DIR), "entries": entries}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def action_patch(data):
    mode = data.get("mode", "full")
    backup_name = f"assistant_pre_deploy_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"
    backup_path = os.path.join(VERSIONS_DIR, backup_name)
    try:
        shutil.copy2(SCRIPT_PATH, backup_path)
    except Exception as e:
        return {"status": "error", "message": f"Backup impossible: {e}"}

    if mode == "full":
        new_code = data.get("code", "")
        if not new_code or len(new_code) < 1000:
            return {"status": "error", "message": "Code too short"}
        checks = _security_checks(new_code)
        if checks:
            return {"status": "error", "message": f"Security: {checks}"}
        try:
            with open(SCRIPT_PATH, "w") as f:
                f.write(new_code)
            return {"status": "ok", "mode": "full", "lines": len(new_code.split("\n")),
                    "backup": backup_name, "timestamp": datetime.now().isoformat()}
        except Exception as e:
            shutil.copy2(backup_path, SCRIPT_PATH)
            return {"status": "error", "message": f"Rollback: {e}"}

    elif mode == "replace":
        old_str = data.get("old_str", "")
        new_str = data.get("new_str", "")
        if not old_str:
            return {"status": "error", "message": "old_str is empty"}
        try:
            with open(SCRIPT_PATH, "r") as f:
                code = f.read()
            count = code.count(old_str)
            if count == 0:
                return {"status": "error", "message": "old_str not found in script"}
            if count > 1:
                return {"status": "error", "message": f"old_str found {count} times — ambiguous"}
            code = code.replace(old_str, new_str)
            checks = _security_checks(code)
            if checks:
                return {"status": "error", "message": f"Security post-patch: {checks}"}
            with open(SCRIPT_PATH, "w") as f:
                f.write(code)
            return {"status": "ok", "mode": "replace", "backup": backup_name,
                    "timestamp": datetime.now().isoformat()}
        except Exception as e:
            shutil.copy2(backup_path, SCRIPT_PATH)
            return {"status": "error", "message": f"Rollback: {e}"}
    return {"status": "error", "message": f"Unknown mode: {mode}"}

def action_test_kill_wrapper():
    """TEST: kills the current wrapper to validate auto-restart by systemd."""
    try:
        r = subprocess.run(["pkill", "-9", "-f", "tunnel_wrapper.sh"],
                           capture_output=True, text=True, timeout=5)
        return {"status": "ok", "rc": r.returncode, "msg": "wrapper killed -9"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def action_timer_status():
    out = {}
    for cmd, label in [
        (["systemctl", "list-timers", "infra_watchdog.timer", "--no-pager"], "list-timers"),
        (["systemctl", "status", "infra_watchdog.timer", "--no-pager"], "status_timer"),
        (["systemctl", "status", "infra_watchdog.service", "--no-pager"], "status_service"),
    ]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            out[label] = r.stdout
        except Exception as e:
            out[label] = str(e)
    return {"status": "ok", "info": out}

def action_kill_zombies():
    """Kills old sudo systemctl status zombies inherited from before."""
    out = []
    try:
        # Current zombies + their children
        # Target old 'sudo systemctl status' processes that haven't terminated
        r = subprocess.run(["bash", "-c",
            "ps -eo pid,etime,cmd | grep -E 'sudo systemctl status|systemctl status [a-z]+$' "
            "| grep -v grep "
            "| awk '$2 ~ /-|h/ || $2 ~ /^[0-9]+:/ {print $1}' "
            "| head -20"],
            capture_output=True, text=True, timeout=5)
        zombies = [int(p) for p in r.stdout.split() if p.strip().isdigit()]
        out.append(f"Zombies found: {zombies}")
        for pid in zombies:
            try:
                # On can not kill a sudo without sudo, mais try graceful shutdown first
                r2 = subprocess.run(["kill", "-TERM", str(pid)],
                                    capture_output=True, text=True, timeout=3)
                if r2.returncode == 0:
                    out.append(f"  kill TERM {pid}: OK")
                else:
                    # Try with sudo (allowed for systemctl but not kill...)
                    out.append(f"  kill TERM {pid}: failed ({r2.stderr.strip()[:80]})")
            except Exception as e:
                out.append(f"  kill {pid}: {e}")
        import time
        time.sleep(2)
        r3 = subprocess.run(["bash", "-c", "ps -eo pid,etime,cmd | grep -E 'sudo systemctl status' | grep -v grep"],
                            capture_output=True, text=True, timeout=5)
        out.append(f"Remaining: {r3.stdout.strip() or '(noe)'}")
    except Exception as e:
        out.append(f"ERR: {e}")
    return {"status": "ok", "log": out}

def action_eliminate_duplicate():
    """Eliminates the cloudflared.service (duplicate).
    Detached to survive a potential tunnel drop during the operation."""
    script = """#!/bin/bash
set -u
exec >> /home/lolufe/assistant/handoff.log 2>&1
echo ""
echo "════════ ELIMINATE_DUPLICATE $(date -Iseconds) ════════"

# 1. Snapshot before
echo "[T0] Services tunnel actives :"
systemctl is-active cloudflared.service && echo "  cloudflared.service: ACTIVE" || echo "  cloudflared.service: inactive"
systemctl is-active cloudflared_tunnel.service && echo "  cloudflared_tunnel.service: ACTIVE" || echo "  cloudflared_tunnel.service: inactive"
echo "[T0] Running wrappers :"
pgrep -af tunnel_wrapper.sh || echo "  (none)"
echo "─────"
sleep 1

# 2. Stop + disable + remove of the DOUBLON
echo "[T1] Stop cloudflared.service (the doublon)"
sudo -n systemctl stop cloudflared.service || true
sleep 1

echo "[T2] Disable cloudflared.service"
sudo -n systemctl disable cloudflared.service || true

echo "[T3] Remove unit file"
sudo -n rm /etc/systemd/system/cloudflared.service || true
# Clean aussi the symlink wants/ s'il existe
sudo -n rm /etc/systemd/system/multi-user.target.wants/cloudflared.service 2>/dev/null || true

echo "[T4] daemon-reload"
sudo -n systemctl daemon-reload

sleep 2
pkill -TERM -f "tunnel_wrapper.sh" 2>/dev/null || true
pkill -TERM -f "cloudflared tunnel --url http://localhost:8501" 2>/dev/null || true
sleep 2
pkill -KILL -f "tunnel_wrapper.sh" 2>/dev/null || true
pkill -KILL -f "cloudflared tunnel --url http://localhost:8501" 2>/dev/null || true

# 4. Settle 5s
echo "[T5] Settle 5s..."
sleep 5
echo "[T5+5s] Wrappers restants :"
pgrep -af tunnel_wrapper.sh || echo "  (none ✅)"

echo "[T6] systemctl start cloudflared_tunnel.service (the seul restant)"
sudo -n systemctl restart cloudflared_tunnel.service

# 6. Wait 12s for the connection + URL publishing
sleep 12

echo "[FINAL] :"
echo "  Wrappers : $(pgrep -af tunnel_wrapper.sh | wc -l)"
echo "  Cloudflared : $(pgrep -f 'cloudflared tunnel.*localhost' | wc -l)"
pgrep -af tunnel_wrapper.sh || true
pgrep -af "cloudflared tunnel" || true
echo "  URL : $(cat /home/lolufe/assistant/tunnel_url.txt 2>/dev/null)"

W=$(pgrep -f 'tunnel_wrapper.sh' | wc -l)
C=$(pgrep -f 'cloudflared tunnel.*localhost' | wc -l)
if [ "$W" = "1" ] && [ "$C" = "1" ]; then
    echo "[OK] PROPRE : 1 wrapper + 1 cloudflared"
else
    echo "[FAIL] $W wrappers, $C cloudflared (should be 1+1)"
fi
echo "════════ FIN ELIMINATE_DUPLICATE ════════"
"""
    p = "/tmp/eliminate_duplicate.sh"
    with open(p, "w") as f: f.write(script)
    os.chmod(p, 0o755)
    subprocess.Popen(["bash", p], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log_deploy("🗑️ Duplicate cloudflared.service elimination scheduled (~30s)")
    return {"status": "ok", "message": "Elimination in progress, wait ~32s"}


def action_inspect_old_cloudflared():
    """Inspects the old cloudflared.service for diagnosis."""
    out = {}
    try:
        r = subprocess.run(["cat", "/etc/systemd/system/cloudflared.service"],
                           capture_output=True, text=True, timeout=5)
        out["unit_content"] = r.stdout
    except Exception as e: out["unit_content"] = f"ERR: {e}"
    try:
        r = subprocess.run(["systemctl", "is-active", "cloudflared.service"],
                           capture_output=True, text=True, timeout=5)
        out["is_active"] = r.stdout.strip()
    except Exception as e: out["is_active"] = f"ERR: {e}"
    try:
        r = subprocess.run(["systemctl", "is-enabled", "cloudflared.service"],
                           capture_output=True, text=True, timeout=5)
        out["is_enabled"] = r.stdout.strip() + " | " + r.stderr.strip()
    except Exception as e: out["is_enabled"] = f"ERR: {e}"
    try:
        r = subprocess.run(["journalctl", "-u", "cloudflared.service", "-n", "20", "--no-pager", "-o", "short-iso"],
                           capture_output=True, text=True, timeout=10)
        out["recent_logs"] = r.stdout
    except Exception as e: out["recent_logs"] = f"ERR: {e}"
    return {"status": "ok", "info": out}

def action_check_orchestrators():
    """Finds all detached shell scripts that could start processes."""
    out = {}
    try:
        # All bash scripts in /tmp with their PID
        r = subprocess.run(["bash", "-c",
            "ls -la /tmp/*.sh 2>/dev/null; echo '---'; "
            "ps -eo pid,ppid,etime,cmd | grep -E 'bash.*\\.sh|systemctl' | grep -v grep"],
            capture_output=True, text=True, timeout=5)
        out["scripts_and_processes"] = r.stdout
    except Exception as e: out["scripts_and_processes"] = f"ERR: {e}"
    # All unit files created
    try:
        r = subprocess.run(["bash", "-c",
            "ls -la /etc/systemd/system/ | grep -v '^d'"],
            capture_output=True, text=True, timeout=5)
        out["unit_files"] = r.stdout
    except Exception as e: out["unit_files"] = f"ERR: {e}"
    # Check actual content of installed unit files
    try:
        r = subprocess.run(["cat", "/etc/systemd/system/cloudflared_tunnel.service"],
                           capture_output=True, text=True, timeout=5)
        out["cloudflared_unit_actual"] = r.stdout
    except Exception as e: out["cloudflared_unit_actual"] = f"ERR: {e}"
    return {"status": "ok", "checks": out}

def action_final_clean():
    """Final atomic cleanup, with checks at each step."""
    script = """#!/bin/bash
set -u
exec >> /home/lolufe/assistant/handoff.log 2>&1
echo ""
echo "════════ FINAL_KEYAN $(date -Iseconds) ════════"
echo "[T0] :"
ps -eo pid,ppid,etime,cmd | grep -E 'tunnel_wrapper|cloudflared.tunnel.*localhost' | grep -v grep || true
echo "─────"
sleep 1

# 1. Stop systemd (control-group kill)
echo "[T1] systemctl stop"
sudo -n systemctl stop cloudflared_tunnel.service
echo "[T1+1s] :"
sleep 1
ps -eo pid,ppid,etime,cmd | grep -E 'tunnel_wrapper|cloudflared.tunnel.*localhost' | grep -v grep || echo "(nothing)"
echo "─────"

# 2. Kill the orphans outside cgroup with retries
for attempt in 1 2 3; do
    pkill -TERM -f "tunnel_wrapper.sh" 2>/dev/null || true
    pkill -TERM -f "cloudflared tunnel --url http://localhost:8501" 2>/dev/null || true
    sleep 1
    pkill -KILL -f "tunnel_wrapper.sh" 2>/dev/null || true
    pkill -KILL -f "cloudflared tunnel --url http://localhost:8501" 2>/dev/null || true
    sleep 1
    REMAINING=$(pgrep -f 'tunnel_wrapper.sh|cloudflared tunnel.*localhost' | wc -l)
    echo "[T2 attempt $attempt] remaining: $REMAINING"
    if [ "$REMAINING" = "0" ]; then break; fi
done

# 3. Long wait: let everything settle (10s)
echo "[T3] settling 10s..."
sleep 10
echo "[T3+10s] :"
ps -eo pid,ppid,etime,cmd | grep -E 'tunnel_wrapper|cloudflared.tunnel.*localhost' | grep -v grep || echo "(nothing)"
echo "─────"

# 4. ONE start
echo "[T4] systemctl start"
sudo -n systemctl start cloudflared_tunnel.service

# 5. Wait 12s for that the tunnel se connecte
sleep 12

echo "[T4+12s] FINAL :"
ps -eo pid,ppid,etime,cmd | grep -E 'tunnel_wrapper|cloudflared.tunnel.*localhost' | grep -v grep
echo "[URL] : $(cat /home/lolufe/assistant/tunnel_url.txt 2>/dev/null)"

W=$(pgrep -f 'tunnel_wrapper.sh' | wc -l)
C=$(pgrep -f 'cloudflared tunnel.*localhost' | wc -l)
echo "[CHECK] wrappers=$W cloudflared=$C"
if [ "$W" = "1" ] && [ "$C" = "1" ]; then
    echo "[OK] KEYAN_STATE_OK"
else
    echo "[FAIL] KEYAN_STATE_KO ($W wrappers, $C cloudflared)"
fi
echo "════════ FIN FINAL_KEYAN ════════"
"""
    p = "/tmp/final_clean.sh"
    with open(p, "w") as f: f.write(script)
    os.chmod(p, 0o755)
    subprocess.Popen(["bash", p], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log_deploy("🧹 Final clean scheduled (~28s)")
    return {"status": "ok", "message": "Final clean in progress, wait ~32s"}


def action_admin_clean_double_tunnel():
    """Kills all wrappers/cloudflared and lets systemd start ONE service.
    Detached because it cuts our channel."""
    script = """#!/bin/bash
set -u
exec >> /home/lolufe/assistant/handoff.log 2>&1
echo ""
echo "════════ KEYAN_DOUBLE $(date -Iseconds) ════════"
echo "[PRE] :"
pgrep -af tunnel_wrapper || true
pgrep -af "cloudflared tunnel" || true
sleep 2
# Stopper proprement the service (KillMode=control-group tue alle the cgroup)
sudo -n systemctl stop cloudflared_tunnel.service
sleep 2
# Kill remaining processes (orphans outside cgroup)
pkill -TERM -f "tunnel_wrapper.sh" 2>/dev/null || true
pkill -TERM -f "cloudflared tunnel --url http://localhost:8501" 2>/dev/null || true
sleep 2
pkill -KILL -f "tunnel_wrapper.sh" 2>/dev/null || true
pkill -KILL -f "cloudflared tunnel --url http://localhost:8501" 2>/dev/null || true
echo "[MID] (after pkill, before start):"
pgrep -af tunnel_wrapper || true
pgrep -af "cloudflared tunnel" || true
sleep 1
sudo -n systemctl start cloudflared_tunnel.service
# Wait the stabilisation (15s)
sleep 15
echo "[POST T+15s] :"
pgrep -af tunnel_wrapper || true
pgrep -af "cloudflared tunnel" || true
echo "[URL] : $(cat /home/lolufe/assistant/tunnel_url.txt 2>/dev/null)"
echo "════════ FIN KEYAN_DOUBLE ════════"
"""
    p = "/tmp/clean_double.sh"
    with open(p, "w") as f: f.write(script)
    os.chmod(p, 0o755)
    subprocess.Popen(["bash", p], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log_deploy("🧹 Double tunnel clean scheduled (~22s)")
    return {"status": "ok", "message": "Final in progress, wait ~25s",
            "expected_new_url": "published on ntfy.sh"}


def action_admin_restart_tunnel():
    """Kills all tunnel processes and restarts cleanly via systemd.
    Detached because it may cut our own channel."""
    script = """#!/bin/bash
set -u
exec >> /home/lolufe/assistant/handoff.log 2>&1
echo ""
echo "════════ TUNNEL_RESTART $(date -Iseconds) ════════"
echo "[PRE] tunnel processes :"
pgrep -af tunnel_wrapper || true
pgrep -af "cloudflared tunnel" || true

# 1. Stopper the service systemd (KillMode=control-group tue alle the cgroup)
sudo -n systemctl stop cloudflared_tunnel.service
sleep 2

pkill -TERM -f "tunnel_wrapper.sh" 2>/dev/null || true
pkill -TERM -f "cloudflared tunnel --url http://localhost:8501" 2>/dev/null || true
sleep 2
pkill -KILL -f "tunnel_wrapper.sh" 2>/dev/null || true
pkill -KILL -f "cloudflared tunnel --url http://localhost:8501" 2>/dev/null || true
sleep 1

sudo -n systemctl start cloudflared_tunnel.service
sleep 8

echo "[POST] tunnel processes :"
pgrep -af tunnel_wrapper || true
pgrep -af "cloudflared tunnel" || true
echo "[POST] URL : $(cat /home/lolufe/assistant/tunnel_url.txt 2>/dev/null)"
echo "════════ FIN TUNNEL_RESTART ════════"
"""
    p = "/tmp/tunnel_restart.sh"
    with open(p, "w") as f: f.write(script)
    os.chmod(p, 0o755)
    subprocess.Popen(["bash", p], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log_deploy("🔄 Tunnel restart scheduled (~12s)")
    return {"status": "ok", "message": "Tunnel restart in progress, wait ~15s",
            "expected_new_url": "published on ntfy.sh"}


def action_admin_cleanup():
    """Stops services + kills all tunnel/cloudflared orphans.
    Does NOT touch the current process."""
    actions = []
    for cmd, label in [
        (["sudo", "-n", "systemctl", "stop", "deploy_server.service"], "stop deploy_server"),
        (["sudo", "-n", "systemctl", "stop", "cloudflared_tunnel.service"], "stop tunnel"),
    ]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            actions.append({"step": label, "rc": r.returncode, "err": r.stderr.strip()[:200]})
        except Exception as e:
            actions.append({"step": label, "error": str(e)})
    time.sleep(1)
    # Kill orphans (no sudo needed for our own user)
    for pattern in ["tunnel_wrapper.sh", "cloudflared tunnel --url http://localhost:8501"]:
        try:
            r = subprocess.run(["pkill", "-9", "-f", pattern],
                               capture_output=True, text=True, timeout=5)
            actions.append({"step": f"pkill {pattern}", "rc": r.returncode})
        except Exception as e:
            actions.append({"step": f"pkill {pattern}", "error": str(e)})
    # Re-check
    diag = action_diag_processes()
    return {"status": "ok", "actions": actions, "after": diag["diag"]}

def action_cgroups():
    """For each tunnel_wrapper and cloudflared, shows its systemd cgroup."""
    out = []
    try:
        r = subprocess.run(["bash", "-c",
            "for p in $(pgrep -f 'tunnel_wrapper.sh|cloudflared tunnel.*localhost'); do "
            "  echo \"=== PID $p ===\"; "
            "  cat /proc/$p/cgroup 2>/dev/null || echo '(no cgroup)'; "
            "done"],
            capture_output=True, text=True, timeout=5)
        return {"status": "ok", "output": r.stdout}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def action_psgroups():
    """Shows pid, ppid, pgid, sid, uid, etime, cmd for wrappers."""
    try:
        r = subprocess.run(
            ["bash", "-c", "ps -eo pid,ppid,pgid,sid,euser,etime,cmd | grep -E 'tunnel_wrapper|cloudflared.tunnel.*localhost' | grep -v grep"],
            capture_output=True, text=True, timeout=5)
        return {"status": "ok", "lines": r.stdout.strip().split("\n")}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def action_check_autostart():
    """Detects anything that could launch tunnel_wrapper.sh automatically."""
    out = {}
    # User crontab
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        out["crontab_user"] = r.stdout if r.returncode == 0 else f"noe ({r.stderr.strip()[:80]})"
    except Exception as e: out["crontab_user"] = f"ERR: {e}"
    # System crontabs
    try:
        r = subprocess.run(["bash", "-c", "ls -la /etc/cron.* /etc/crontab 2>/dev/null && grep -r tunnel /etc/cron.* /etc/crontab 2>/dev/null"],
                           capture_output=True, text=True, timeout=5)
        out["crontab_system"] = r.stdout
    except Exception as e: out["crontab_system"] = f"ERR: {e}"
    # systemd timers
    try:
        r = subprocess.run(["systemctl", "list-timers", "--no-pager"],
                           capture_output=True, text=True, timeout=5)
        out["systemd_timers"] = r.stdout
    except Exception as e: out["systemd_timers"] = f"ERR: {e}"
    # Search for tunnel_wrapper in any startup script
    try:
        r = subprocess.run(["bash", "-c",
            "grep -rl tunnel_wrapper /etc/systemd /home/lolufe 2>/dev/null | head -20"],
            capture_output=True, text=True, timeout=10)
        out["files_referencing_wrapper"] = r.stdout.strip().split("\n")
    except Exception as e: out["files_referencing_wrapper"] = f"ERR: {e}"
    # bashrc, profile, etc
    try:
        r = subprocess.run(["bash", "-c",
            "grep -l 'tunnel_wrapper|cloudflared|deploy_server' /home/lolufe/.bashrc /home/lolufe/.profile /home/lolufe/.bash_profile /etc/rc.local 2>/dev/null"],
            capture_output=True, text=True, timeout=5)
        out["shell_init_files"] = r.stdout
    except Exception as e: out["shell_init_files"] = f"ERR: {e}"
    return {"status": "ok", "checks": out}

def action_pstree():
    """pstree to see the full process tree of wrappers and cloudflared."""
    results = {}
    for label, pid_pattern in [("tunnel_wrappers", "tunnel_wrapper.sh"),
                                ("cloudflared", "cloudflared tunnel")]:
        try:
            r = subprocess.run(
                ["bash", "-c", f"pgrep -f '{pid_pattern}' | head -10 | while read p; do echo '--- PID '$p; pstree -p $p; done"],
                capture_output=True, text=True, timeout=5)
            results[label] = r.stdout.strip().split("\n")
        except Exception as e:
            results[label] = [str(e)]
    return {"status": "ok", "trees": results}

def action_journalctl(service, n=30):
    """Reads the last n systemd log entries for a service."""
    try:
        r = subprocess.run(
            ["journalctl", "-u", service, "-n", str(n), "--no-pager", "-o", "short-iso"],
            capture_output=True, text=True, timeout=10)
        return {"status": "ok", "service": service,
                "lines": r.stdout.split("\n"),
                "stderr": r.stderr.strip()[:500]}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def action_diag_processes():
    """Inventory of processes related to deploy_server, cloudflared, tunnel_wrapper.
    Format: PID PPID CMD to understand process trees."""
    out = {}
    for pattern in ["deploy_server.py", "cloudflared", "tunnel_wrapper"]:
        try:
            # ps with PID, PPID for see the relationships
            r = subprocess.run(
                ["bash", "-c", f"pgrep -f '{pattern}' | xargs -r ps -o pid,ppid,etime,cmd -p"],
                capture_output=True, text=True, timeout=5)
            out[pattern] = [l for l in r.stdout.strip().split("\n") if l]
        except Exception as e:
            out[pattern] = [f"ERR: {e}"]
    # Who is listening on 8501?
    try:
        r = subprocess.run(["ss", "-tlnp", "sport", "=", ":8501"],
                           capture_output=True, text=True, timeout=5)
        out["port_8501"] = r.stdout.strip().split("\n")
    except Exception as e:
        out["port_8501"] = [f"ERR: {e}"]
    out["my_pid"] = os.getpid()
    return {"status": "ok", "diag": out}

def action_probe_sudo():
    """No-destructive test of sudo rights required for infra bootstrap."""
    # Create a test file for the cp command
    try:
        with open("/tmp/test.service", "w") as _f:
            _f.write("[Unit]\nDescription=Probe Test\n")
    except Exception:
        pass
    tests = [
        (["sudo", "-n", "systemctl", "is-active", "deploy_server.service"], "sc_is-active"),
        (["sudo", "-n", "systemctl", "daemon-reload"], "sc_daemon-reload"),
        (["sudo", "-n", "systemctl", "enable", "deploy_server.service"], "sc_enable"),
        (["sudo", "-n", "cp", "/tmp/test.service", "/etc/systemd/system/test.service"], "cp_unit"),
        (["sudo", "-n", "cp", "/etc/hostname", "/tmp/__sudo_cp_probe"], "cp_arbitrary_should_fail"),
    ]
    results = {}
    for cmd, label in tests:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            results[label] = {"rc": r.returncode,
                              "stdout": (r.stdout or "").strip()[:4000],
                              "stderr": (r.stderr or "").strip()[:500]}
        except Exception as e:
            results[label] = {"error": str(e)[:200]}
    # Cleanup
    for cleanup in [
        ["sudo", "-n", "rm", "/etc/systemd/system/test.service"],
        ["rm", "-f", "/tmp/test.service", "/tmp/__sudo_cp_probe"],
    ]:
        try: subprocess.run(cleanup, capture_output=True, timeout=5)
        except: pass
    ok = all(r.get("rc") == 0 for r in results.values())
    return {"status": "ok" if ok else "partial", "can_bootstrap": ok, "tests": results}


def action_infra_install():
    """Installs systemd unit files + daemon-reload + enable.
    Does NOT perform start/restart — separate handoff."""
    try:
        # Write to /tmp (owned by user, no sudo needed)
        with open("/tmp/deploy_server.service", "w") as f:
            f.write(DEPLOY_SERVER_UNIT)
        with open("/tmp/cloudflared_tunnel.service", "w") as f:
            f.write(CLOUDFLARED_TUNNEL_UNIT)

        steps = []
        for unit in ["deploy_server.service", "cloudflared_tunnel.service"]:
            r = subprocess.run(
                ["sudo", "-n", "cp", f"/tmp/{unit}", f"/etc/systemd/system/{unit}"],
                capture_output=True, text=True, timeout=10)
            steps.append((f"cp {unit}", r.returncode, (r.stderr or "").strip()[:200]))
            if r.returncode != 0:
                return {"status": "error", "steps": steps}

        r = subprocess.run(["sudo", "-n", "systemctl", "daemon-reload"],
                           capture_output=True, text=True, timeout=10)
        steps.append(("daemon-reload", r.returncode, (r.stderr or "").strip()[:200]))
        if r.returncode != 0:
            return {"status": "error", "steps": steps}

        # Watchdog: service + timer
        with open("/tmp/infra_watchdog.service", "w") as f:
            f.write(WATCHDOG_SERVICE_UNIT)
        with open("/tmp/infra_watchdog.timer", "w") as f:
            f.write(WATCHDOG_TIMER_UNIT)
        for fname in ["infra_watchdog.service", "infra_watchdog.timer"]:
            r = subprocess.run(
                ["sudo", "-n", "cp", f"/tmp/{fname}",
                 f"/etc/systemd/system/{fname}"],
                capture_output=True, text=True, timeout=10)
            steps.append((f"cp {fname}", r.returncode, (r.stderr or "").strip()[:200]))
            if r.returncode != 0:
                return {"status": "error", "steps": steps}

        # Write the watchdog script (executable by user, no sudo needed)
        watchdog_script = """#!/bin/bash
LOG="/home/lolufe/assistant/watchdog.log"
URL_FILE="/home/lolufe/assistant/tunnel_url.txt"
STATE_FILE="/home/lolufe/assistant/watchdog.state"

log() { echo "[$(date -Iseconds)] $*"; }

# 1. Test deploy_server local
if ! curl -sf -m 5 http://127.0.0.1:8501/ping >/dev/null 2>&1; then
    log "❌ deploy_server local KO → restart"
    sudo -n systemctl restart deploy_server.service
    echo "deploy_restarted=$(date -Iseconds)" > "$STATE_FILE"
    exit 0
fi

# 2. Test tunnel externe
URL=$(cat "$URL_FILE" 2>/dev/null)
if [ -z "$URL" ]; then
    log "⚠️  not of URL in $URL_FILE → restart tunnel"
    sudo -n systemctl restart cloudflared_tunnel.service
    exit 0
fi

# 3. Test ping via the tunnel (curl does not follow auth so we accept 401)
HTTP=$(curl -s -m 8 -o /dev/null -w "%{http_code}" "$URL/ping" 2>/dev/null)
if [ "$HTTP" != "401" ] && [ "$HTTP" != "200" ]; then
    log "⚠️  tunnel KO (HTTP=$HTTP) sur $URL → restart"
    sudo -n systemctl restart cloudflared_tunnel.service
    echo "tunnel_restarted=$(date -Iseconds)" > "$STATE_FILE"
fi

SIZE=$(stat -c %s "$LOG" 2>/dev/null || echo 0)
if [ "$SIZE" -gt 204800 ]; then
    tail -500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
"""
        with open("/home/lolufe/assistant/watchdog.sh", "w") as f:
            f.write(watchdog_script)
        os.chmod("/home/lolufe/assistant/watchdog.sh", 0o755)
        steps.append(("write watchdog.sh", 0, ""))

        # daemon-reload + enable + start timer
        subprocess.run(["sudo", "-n", "systemctl", "daemon-reload"], timeout=10, capture_output=True)
        for svc in ["deploy_server.service", "cloudflared_tunnel.service", "infra_watchdog.timer"]:
            r = subprocess.run(["sudo", "-n", "systemctl", "enable", svc],
                               capture_output=True, text=True, timeout=10)
            steps.append((f"enable {svc}", r.returncode,
                          (r.stderr or r.stdout or "").strip()[:200]))
        r = subprocess.run(["sudo", "-n", "systemctl", "start", "infra_watchdog.timer"],
                           capture_output=True, text=True, timeout=10)
        steps.append(("start infra_watchdog.timer", r.returncode, (r.stderr or "").strip()[:200]))

        log_deploy("✅ Systemd units + watchdog installed + enabled at boot")
        return {"status": "ok", "steps": steps,
                "installed": ["deploy_server.service", "cloudflared_tunnel.service",
                              "infra_watchdog.service", "infra_watchdog.timer"]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def action_infra_status():
    """Status of all services + current tunnel URL."""
    services = ["deploy_server.service", "cloudflared_tunnel.service", "assistant.service"]
    result = {}
    for svc in services:
        try:
            a = subprocess.run(["systemctl", "is-active", svc],
                               capture_output=True, text=True, timeout=5)
            e = subprocess.run(["systemctl", "is-enabled", svc],
                               capture_output=True, text=True, timeout=5)
            p = subprocess.run(["systemctl", "show", svc, "--property=MainPID,ExecMainStartTimestamp"],
                               capture_output=True, text=True, timeout=5)
            result[svc] = {
                "active": a.stdout.strip() or a.stderr.strip()[:60],
                "enabled": e.stdout.strip() or e.stderr.strip()[:60],
                "info": p.stdout.strip().replace("\n", " | ")[:200]
            }
        except Exception as ex:
            result[svc] = {"error": str(ex)[:200]}

    tunnel_url = None
    try:
        with open(TUNNEL_URL_FILE) as f:
            tunnel_url = f.read().strip()
    except Exception:
        pass

    # Am I running under systemd?
    my_pid = os.getpid()
    ps = subprocess.run(["ps", "-o", "ppid,cmd", "-p", str(my_pid)],
                        capture_output=True, text=True, timeout=5)
    ppid_line = ps.stdout.strip().split("\n")[-1] if ps.stdout else ""
    under_systemd = False
    try:
        ppid = int(ppid_line.strip().split()[0])
        # If PPID == 1 (init/systemd), we're managed by systemd
        under_systemd = (ppid == 1)
    except Exception:
        pass

    return {"status": "ok", "services": result, "tunnel_url": tunnel_url,
            "my_pid": my_pid, "my_ppid_line": ppid_line,
            "under_systemd": under_systemd,
            "timestamp": datetime.now().isoformat()}


def action_infra_handoff():
    """Atomic handoff to systemd. Strategy:
    1. Response sent immediately
    2. Detached script: kills nohup orphans, waits for them to die,
       starts systemd services, checks their health,
       republishes URL on ntfy IF everything is OK.
    """
    my_pid = os.getpid()
    script = f"""#!/bin/bash
set -u
exec >> /home/lolufe/assistant/handoff.log 2>&1
echo ""
echo "════════ HANDOFF $(date -Iseconds) ════════"
echo "[PRE] my_pid (deploy_server to kill): {my_pid}"
echo "[PRE] processes deploy_server :"
pgrep -af deploy_server.py || true
echo "[PRE] processes tunnel :"
pgrep -af tunnel_wrapper || true
pgrep -af "cloudflared tunnel" || true
echo "[PRE] port 8501 :"
ss -tlnp sport = :8501 || true

sleep 3

# 1. Kill TOUS the wrappers and cloudflared orphans (lolufe can the faire without sudo)
echo "[KILL] orphans tunnel..."
pkill -TERM -f "tunnel_wrapper.sh" 2>/dev/null || true
pkill -TERM -f "cloudflared tunnel --url http://localhost:8501" 2>/dev/null || true
sleep 2
pkill -KILL -f "tunnel_wrapper.sh" 2>/dev/null || true
pkill -KILL -f "cloudflared tunnel --url http://localhost:8501" 2>/dev/null || true

echo "[KILL] my deploy_server pid {my_pid}"
kill -TERM {my_pid} 2>/dev/null || true
sleep 2
kill -KILL {my_pid} 2>/dev/null || true

for i in 1 2 3 4 5; do
    if ! ss -tln sport = :8501 | grep -q LISTEN; then
        echo "[OK] port 8501 libre"
        break
    fi
    echo "[WAIT] port 8501 still busy (attempt $i/5)"
    sleep 1
done

echo "[START] deploy_server.service"
sudo -n systemctl start deploy_server.service
sleep 3

DEPLOY_OK=0
for i in 1 2 3 4 5; do
    if curl -sf -m 3 -H "Authorization: Bearer ${{DS_SECRET:-}}" http://127.0.0.1:8501/ping >/dev/null 2>&1; then
        DEPLOY_OK=1
        echo "[OK] deploy_server responding (attempt $i)"
        break
    fi
    if curl -s -m 3 http://127.0.0.1:8501/ping 2>/dev/null | grep -q -E "(alive|Unauthorized)"; then
        DEPLOY_OK=1
        echo "[OK] deploy_server listening (attempt $i)"
        break
    fi
    sleep 2
done

if [ "$DEPLOY_OK" != "1" ]; then
    echo "[FAIL] deploy_server not responding after 5 attempts"
    sudo -n systemctl status deploy_server.service --no-pager || true
fi

echo "[START] cloudflared_tunnel.service"
sudo -n systemctl start cloudflared_tunnel.service

# 7. Wait URL (max 30s)
URL=""
for i in $(seq 1 30); do
    if [ -f /home/lolufe/assistant/tunnel_url.txt ]; then
        CANDIDATE=$(cat /home/lolufe/assistant/tunnel_url.txt 2>/dev/null)
        FILE_AGE=$(($(date +%s) - $(stat -c %Y /home/lolufe/assistant/tunnel_url.txt 2>/dev/null || echo 0)))
        if [ -n "$CANDIDATE" ] && [ "$FILE_AGE" -lt 60 ]; then
            URL="$CANDIDATE"
            echo "[OK] URL published by tunnel ($FILE_AGE s): $URL"
            break
        fi
    fi
    sleep 1
done

if [ -z "$URL" ]; then
    echo "[WARN] Tunnel URL not published after 30s — wrapper may have failed"
    sudo -n systemctl status cloudflared_tunnel.service --no-pager || true
fi

echo ""
echo "[FINAL] processes deploy_server :"
pgrep -af deploy_server.py || true
echo "[FINAL] processes tunnel :"
pgrep -af tunnel_wrapper || true
pgrep -af "cloudflared tunnel" || true
echo "[FINAL] port 8501 :"
ss -tlnp sport = :8501 || true
echo "[FINAL] services :"
echo "  deploy_server : $(sudo -n systemctl is-active deploy_server.service)"
echo "  tunnel        : $(sudo -n systemctl is-active cloudflared_tunnel.service)"
echo "[FINAL] URL : $URL"
echo "════════ FIN HANDOFF $(date -Iseconds) ════════"
"""
    script_path = "/tmp/infra_handoff.sh"
    with open(script_path, "w") as f:
        f.write(script)
    os.chmod(script_path, 0o755)
    subprocess.Popen(["bash", script_path], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log_deploy(f"🔄 Systemd handoff scheduled in 3s (pid {my_pid} will be killed)")
    return {"status": "ok", "message": "Systemd handoff in progress (~10s)",
            "pid_to_kill": my_pid,
            "expected_new_tunnel_url": "published on ntfy.sh after ~5s"}


def _heartbeat_loop():
    """Every HEARTBEAT_INTERVAL seconds, republishes the tunnel URL on ntfy.sh.
    Keeps the URL fresh within ntfy's 24h window, even if the tunnel hasn't restarted."""
    while True:
        try:
            time.sleep(HEARTBEAT_INTERVAL)
            if os.path.exists(TUNNEL_URL_FILE):
                with open(TUNNEL_URL_FILE) as f:
                    url = f.read().strip()
                if url.startswith("https://"):
                    subprocess.run(
                        ["curl", "-s", "-m", "10", "-d", url, f"https://ntfy.sh/{NTFY_TOPIC}"],
                        capture_output=True, timeout=15)
                    log_deploy(f"💓 heartbeat: {url}")
        except Exception as e:
            log_deploy(f"❌ heartbeat: {e}")


def action_restart_self():
    """Re-exec the deploy_server itself (bootstrap a new version).
    Response sent first, then exec in a thread after 1s."""
    def _delayed_exec():
        time.sleep(1)
        log_deploy("🔄 Deploy Server self-restart (os.execv)")
        try:
            os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
        except Exception as e:
            log_deploy(f"❌ execv failed: {e}")
            os._exit(1)
    threading.Thread(target=_delayed_exec, daemon=False).start()
    return {"status": "ok", "message": "deploy_server restarting in ~1s",
            "timestamp": datetime.now().isoformat()}

def action_restart():
    try:
        subprocess.run(["sudo", "-n", "systemctl", "restart", "assistant.service"], capture_output=True, text=True, timeout=30)
        time.sleep(2)
        status = subprocess.run(["sudo", "-n", "systemctl", "is-active", "assistant.service"], capture_output=True, text=True, timeout=10)
        is_active = status.stdout.strip() == "active"
        if is_active:
            log_deploy("✅ assistant.service restarted")
            return {"status": "ok", "service": "active", "timestamp": datetime.now().isoformat()}
        return {"status": "error", "service": status.stdout.strip()}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def action_rollback():
    try:
        backups = sorted([f for f in os.listdir(VERSIONS_DIR) if f.startswith("assistant_pre_deploy_")])
        if not backups:
            return {"status": "error", "message": "No backup found"}
        last_backup = os.path.join(VERSIONS_DIR, backups[-1])
        shutil.copy2(last_backup, SCRIPT_PATH)
        log_deploy(f"✅ Rollback vers {backups[-1]}")
        subprocess.run(["sudo", "-n", "systemctl", "restart", "assistant.service"], capture_output=True, timeout=30)
        return {"status": "ok", "restored": backups[-1], "timestamp": datetime.now().isoformat()}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def action_status():
    try:
        result = subprocess.run(["sudo", "systemctl", "is-active", "assistant"], capture_output=True, text=True, timeout=10)
        with open(SCRIPT_PATH, "r") as f:
            code = f.read()
        backups = sorted([f for f in os.listdir(VERSIONS_DIR) if f.endswith(".py")])
        return {"status": "ok", "service": result.stdout.strip(),
                "script_lines": len(code.split("\n")), "script_size_kb": round(len(code) / 1024, 1),
                "version": _extract_version(code), "backups": len(backups),
                "last_backup": backups[-1] if backups else None, "timestamp": datetime.now().isoformat()}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def action_logs(n=50):
    try:
        with open("/home/lolufe/assistant/assistant.log", "r") as f:
            lines = f.readlines()
        return {"status": "ok", "lines": [l.rstrip() for l in lines[-n:]], "total_lines": len(lines)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# === HTTP HANDLER ===

class DeployHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not verify_token_simple(self.headers):
            self._respond(401, {"status": "error", "message": "Unauthorized"})
            return
        if self.path == "/read":
            self._respond(200, action_read())
        elif self.path.startswith("/read/"):
            self._respond(200, action_read(self.path[6:]))
        elif self.path == "/status":
            self._respond(200, action_status())
        elif self.path.startswith("/ask"):
            # API vocale for Google Home / Alexa
            import urllib.parse
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            question = params.get("q", [""])[0]
            if not question:
                self._respond(400, {"status": "error", "message": "Missing parameter q"})
            else:
                try:
                    # Write the question to a file, the assistant handles it
                    q_path = os.path.join(ASSISTANT_DIR, "vocal_question.json")
                    a_path = os.path.join(ASSISTANT_DIR, "vocal_answer.json")
                    import time as _t
                    with open(q_path, "w") as f:
                        json.dump({"q": question, "ts": _t.time()}, f)
                    for _ in range(60):
                        _t.sleep(0.5)
                        if os.path.exists(a_path):
                            try:
                                with open(a_path) as f:
                                    answer = json.load(f)
                                if answer.get("ts", 0) > _t.time() - 35:
                                    os.remove(a_path)
                                    self._respond(200, answer)
                                    return
                            except Exception:
                                pass
                    self._respond(504, {"status": "error", "message": "Timeout"})
                except Exception as e:
                    self._respond(500, {"status": "error", "message": str(e)})
        elif self.path.startswith("/logs"):
            n = 50
            if "n=" in self.path:
                try: n = int(self.path.split("n=")[1])
                except: pass
            self._respond(200, action_logs(n))
        elif self.path == "/ping":
            self._respond(200, {"status": "ok", "message": "deploy_server v2 alive"})
        elif self.path == "/infra_status":
            self._respond(200, action_infra_status())
        elif self.path == "/admin_cleanup":
            self._respond(200, action_admin_cleanup())
        elif self.path == "/admin_restart_tunnel":
            self._respond(200, action_admin_restart_tunnel())
        elif self.path == "/admin_clean_double":
            self._respond(200, action_admin_clean_double_tunnel())
        elif self.path == "/final_clean":
            self._respond(200, action_final_clean())
        elif self.path == "/check_orchestrators":
            self._respond(200, action_check_orchestrators())
        elif self.path == "/inspect_old_cloudflared":
            self._respond(200, action_inspect_old_cloudflared())
        elif self.path == "/eliminate_duplicate":
            self._respond(200, action_eliminate_duplicate())
        elif self.path == "/test_kill_wrapper":
            self._respond(200, action_test_kill_wrapper())
        elif self.path == "/timer_status":
            self._respond(200, action_timer_status())
        elif self.path == "/kill_zombies":
            self._respond(200, action_kill_zombies())
        elif self.path == "/diag_processes":
            self._respond(200, action_diag_processes())
        elif self.path.startswith("/journalctl/"):
            svc = self.path[len("/journalctl/"):].split("?")[0]
            self._respond(200, action_journalctl(svc))
        elif self.path == "/pstree":
            self._respond(200, action_pstree())
        elif self.path == "/check_autostart":
            self._respond(200, action_check_autostart())
        elif self.path == "/psgroups":
            self._respond(200, action_psgroups())
        elif self.path == "/sudo_probe":
            self._respond(200, action_probe_sudo())
        elif self.path.startswith("/ls"):
            subdir = self.path[4:] if len(self.path) > 4 else ""
            self._respond(200, action_list_files(subdir))
        else:
            self._respond(404, {"status": "error", "message": "Unknown endpoint"})

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        if not verify_auth(self.headers, body):
            self._respond(401, {"status": "error", "message": "Unauthorized"})
            return
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._respond(400, {"status": "error", "message": "Invalid JSON"})
            return
        if self.path == "/patch":
            self._respond(200, action_patch(data))
        elif self.path == "/restart":
            self._respond(200, action_restart())
        elif self.path == "/restart_self":
            self._respond(200, action_restart_self())
        elif self.path == "/infra_install":
            self._respond(200, action_infra_install())
        elif self.path == "/infra_handoff":
            self._respond(200, action_infra_handoff())
        elif self.path == "/rollback":
            self._respond(200, action_rollback())
        elif self.path == "/file":
            self._respond(200, action_write_file(data))
        elif self.path == "/delete":
            self._respond(200, action_delete_file(data))
        elif self.path == "/chmod":
            self._respond(200, action_chmod(data))
        elif self.path == "/run_v2_push":
            self._respond(200, action_run_v2_push())
        elif self.path == "/deploy":
            patch_result = action_patch(data)
            if patch_result["status"] != "ok":
                self._respond(200, patch_result)
                return
            restart_result = action_restart()
            self._respond(200, {"status": "ok" if restart_result["status"] == "ok" else "partial",
                "patch": patch_result, "restart": restart_result, "timestamp": datetime.now().isoformat()})
        else:
            self._respond(404, {"status": "error", "message": "Unknown endpoint"})

    def _respond(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def log_message(self, format, *args):
        log_deploy(f"HTTP {args[0] if args else ''}")

def main():
    os.makedirs(VERSIONS_DIR, exist_ok=True)
    log_deploy(f"🚀 Deploy Server v2 started on port {PORT}")
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    log_deploy(f"💓 ntfy.sh heartbeat enabled (every {HEARTBEAT_INTERVAL}s)")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), DeployHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log_deploy("🛑 Deploy Server stopped")
        server.server_close()

if __name__ == "__main__":
    main()
