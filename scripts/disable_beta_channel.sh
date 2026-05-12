#!/usr/bin/env bash
# Disable the beta tester channel

set -euo pipefail

BLUE="\033[1;34m"; GREEN="\033[1;32m"; NC="\033[0m"
info() { echo -e "${BLUE}ℹ${NC} $*"; }
ok()   { echo -e "${GREEN}✓${NC} $*"; }

if [ "$EUID" -ne 0 ]; then
    info "Requesting sudo..."
    exec sudo -E bash "$0" "$@"
fi

for svc in assistant-deploy.service assistant-tunnel.service; do
    if systemctl is-enabled "$svc" >/dev/null 2>&1 || systemctl is-active "$svc" >/dev/null 2>&1; then
        info "Stopping $svc"
        systemctl stop "$svc"    || true
        systemctl disable "$svc" || true
        rm -f "/etc/systemd/system/$svc"
        ok "$svc removed"
    fi
done

# Kill orphan processes
pkill -TERM -f "tunnel_wrapper.sh" 2>/dev/null || true
pkill -TERM -f "cloudflared tunnel --url http://localhost:8501" 2>/dev/null || true
sleep 2
pkill -KILL -f "tunnel_wrapper.sh" 2>/dev/null || true
pkill -KILL -f "cloudflared tunnel --url http://localhost:8501" 2>/dev/null || true

systemctl daemon-reload
ok "Beta tester mode disabled"
echo
echo "AI Companion continues running normally."
echo "Your data (config.json, memory.db) is intact."
