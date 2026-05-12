#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# Deploy assistant as a systemd service (native Linux / Pi / VM)
# Requires sudo to install /etc/systemd/system/assistant.service
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="assistant.service"
TEMPLATE="${INSTALL_DIR}/assistant.service.template"
TARGET="/etc/systemd/system/${SERVICE_NAME}"
USER_NAME="${SUDO_USER:-$USER}"

BLUE="\033[1;34m"; GREEN="\033[1;32m"; RED="\033[1;31m"; NC="\033[0m"
info() { echo -e "${BLUE}ℹ${NC} $*"; }
ok()   { echo -e "${GREEN}✓${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*" >&2; exit 1; }

[ -f "$TEMPLATE" ]                            || fail "Template not found : $TEMPLATE"
[ -f "${INSTALL_DIR}/assistant.py" ]          || fail "assistant.py not found in $INSTALL_DIR"
[ -f "${INSTALL_DIR}/config.json" ]           || fail "config.json missing - run ./install.sh first"

if [ "$EUID" -ne 0 ]; then
    info "This script must be run with sudo to write to /etc/systemd/system/"
    exec sudo -E "$0" "$@"
fi

info "Installing service for user : $USER_NAME"
info "Installation directory : $INSTALL_DIR"

# Generate the service file from the template
sed -e "s|__USER__|${USER_NAME}|g" \
    -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
    "$TEMPLATE" > "$TARGET"
chmod 644 "$TARGET"
ok "Service installed : $TARGET"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
ok "Service enabled at boot"

info "Starting service..."
systemctl start "$SERVICE_NAME"
sleep 2

if systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "Service active : $(systemctl is-active $SERVICE_NAME)"
else
    fail "Service did not start. Logs : journalctl -u $SERVICE_NAME -n 30"
fi

echo
echo "Useful commands :"
echo "  sudo systemctl status  $SERVICE_NAME    # Status"
echo "  sudo systemctl restart $SERVICE_NAME    # Restart"
echo "  sudo systemctl stop    $SERVICE_NAME    # Stop"
echo "  sudo journalctl -u $SERVICE_NAME -f     # Logs live"
echo "  tail -f ${INSTALL_DIR}/assistant.log    # Application logs"
