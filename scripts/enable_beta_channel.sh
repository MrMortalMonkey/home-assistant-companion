#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# Enable the beta tester channel : deploy_server + tunnel Cloudflare
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BLUE="\033[1;34m"; GREEN="\033[1;32m"; YELLOW="\033[1;33m"; RED="\033[1;31m"; NC="\033[0m"
info() { echo -e "${BLUE}ℹ${NC} $*"; }
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*" >&2; exit 1; }

cat <<WARN

${YELLOW}════════════════════════════════════════════════════════════${NC}
  ${BOLD:-}You are about to enable beta tester mode.${NC}
  This exposes your installation to remote patches.
  Read docs/BETA_CHANNEL.md if you have not already done so.
${YELLOW}════════════════════════════════════════════════════════════${NC}

WARN
read -r -p "Continue? type YES in uppercase: " confirm
[ "$confirm" = "YES" ] || fail "Canceled"

[ -f "${INSTALL_DIR}/config.json" ]       || fail "config.json missing - run ./install.sh first"
[ -f "${INSTALL_DIR}/deploy_server.py" ]  || fail "deploy_server.py missing in $INSTALL_DIR"
command -v cloudflared >/dev/null 2>&1    || fail "cloudflared is not installed - see docs/BETA_CHANNEL.md"
command -v jq          >/dev/null 2>&1    || fail "jq is not installed (sudo apt install jq)"

USER_NAME="${SUDO_USER:-$USER}"
DEPLOY_SECRET=$(jq -r .deploy_secret "${INSTALL_DIR}/config.json")
[ -n "$DEPLOY_SECRET" ] && [ "$DEPLOY_SECRET" != "null" ] || fail "deploy_secret missing from config.json"

# ntfy topic = prefix + short hash of the installation secret
TOPIC_SUFFIX=$(echo -n "$DEPLOY_SECRET" | sha256sum | cut -c1-16)
NTFY_TOPIC="assistant-beta-${TOPIC_SUFFIX}"

info "ntfy topic for this installation : ${BOLD:-}${NTFY_TOPIC}${NC}"

if [ "$EUID" -ne 0 ]; then
    info "This script needs sudo to install systemd services"
    exec sudo -E INSTALL_DIR="$INSTALL_DIR" NTFY_TOPIC="$NTFY_TOPIC" USER_NAME="$USER_NAME" bash "$0" --sudo-phase
fi


# 1. Wrapper tunnel
cat > "${INSTALL_DIR}/tunnel_wrapper.sh" <<WRAPPER
#!/bin/bash
# Cloudflare Tunnel wrapper: capture URL and publish it to ntfy
set -u
TOPIC="${NTFY_TOPIC}"
URL_FILE="${INSTALL_DIR}/tunnel_url.txt"

TMP=\$(mktemp)
cloudflared tunnel --url http://localhost:8501 > "\$TMP" 2>&1 &
CFD=\$!

cleanup() { kill -TERM \$CFD 2>/dev/null || true; wait \$CFD 2>/dev/null; rm -f "\$TMP"; exit 0; }
trap cleanup TERM INT

tail -f "\$TMP" &
TAIL=\$!

for i in \$(seq 1 60); do
    URL=\$(grep -oE 'https://[a-z0-9-]+\\.trycloudflare\\.com' "\$TMP" 2>/dev/null | head -1)
    if [ -n "\$URL" ]; then
        echo "\$URL" > "\$URL_FILE"
        echo ">>> URL_PUBLISHED: \$URL"
        curl -s -m 10 -d "\$URL" "https://ntfy.sh/\$TOPIC" >/dev/null
        break
    fi
    kill -0 \$CFD 2>/dev/null || { kill \$TAIL 2>/dev/null; rm -f "\$TMP"; exit 1; }
    sleep 0.5
done

wait \$CFD
EXIT=\$?
kill \$TAIL 2>/dev/null
rm -f "\$TMP"
exit \$EXIT
WRAPPER
chmod +x "${INSTALL_DIR}/tunnel_wrapper.sh"
chown "${USER_NAME}:${USER_NAME}" "${INSTALL_DIR}/tunnel_wrapper.sh"
ok "Tunnel wrapper created"

# 2. Unit file deploy_server
cat > /etc/systemd/system/assistant-deploy.service <<UNIT
[Unit]
Description=AI Companion Deploy Server (beta channel)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${INSTALL_DIR}
ExecStart=/usr/bin/python3 -u ${INSTALL_DIR}/deploy_server.py
Restart=always
RestartSec=3
StandardOutput=append:${INSTALL_DIR}/deploy_server.log
StandardError=append:${INSTALL_DIR}/deploy_server.log
KillMode=control-group
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
UNIT

# 3. Unit file tunnel
cat > /etc/systemd/system/assistant-tunnel.service <<UNIT
[Unit]
Description=AI Companion Cloudflare Tunnel (beta channel)
After=network-online.target assistant-deploy.service
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${INSTALL_DIR}
ExecStart=/bin/bash ${INSTALL_DIR}/tunnel_wrapper.sh
Restart=always
RestartSec=5
StandardOutput=append:${INSTALL_DIR}/tunnel.log
StandardError=append:${INSTALL_DIR}/tunnel.log
KillMode=control-group
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable assistant-deploy.service assistant-tunnel.service
systemctl start  assistant-deploy.service
sleep 2
systemctl start  assistant-tunnel.service

ok "Services installed and started"

# Wait for the URL
info "Waiting for the first tunnel URL (max 30s)..."
for i in $(seq 1 30); do
    if [ -f "${INSTALL_DIR}/tunnel_url.txt" ]; then
        URL=$(cat "${INSTALL_DIR}/tunnel_url.txt")
        [ -n "$URL" ] && break
    fi
    sleep 1
done

echo
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Beta tester mode enabled${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo
echo "  Current tunnel URL : ${URL:-(not published yet)}"
echo "  Topic ntfy          : ${NTFY_TOPIC}"
echo
echo "  📤 Send this topic to MrMortalMonkey through a private channel"
echo "     so patches can be pushed to you."
echo
echo "  Disable : ./scripts/disable_beta_channel.sh"
echo
