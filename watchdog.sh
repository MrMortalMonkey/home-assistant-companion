#!/bin/bash
# AI Assistant watchdog - checks local deploy_server and external tunnel
#
# Fix (2026-04-24): the local test now accepts 401 like 200.
# A 401 proves deploy_server is listening and processed the request; it only
# rejected missing auth. The previous version used curl -sf, which failed on
# every HTTP status >= 400 and restarted deploy_server every 2 minutes.

LOG="/home/lolufe/assistant/watchdog.log"
URL_FILE="/home/lolufe/assistant/tunnel_url.txt"
STATE_FILE="/home/lolufe/assistant/watchdog.state"

log() { echo "[$(date -Iseconds)] $*" >> "$LOG"; }

# Test a URL: return the HTTP code, or "000" if unreachable.
http_code() {
    curl -s -m 5 -o /dev/null -w "%{http_code}" "$1" 2>/dev/null
}

# A live deploy_server returns 200 with auth or 401 without auth.
# Any other code or timeout is treated as unhealthy.
is_alive() {
    local code="$1"
    [ "$code" = "200" ] || [ "$code" = "401" ]
}

# 1. Test local deploy_server.
LOCAL_CODE=$(http_code "http://127.0.0.1:8501/ping")
if ! is_alive "$LOCAL_CODE"; then
    log "❌ deploy_server local KO (HTTP=$LOCAL_CODE) → restart"
    sudo -n systemctl restart deploy_server.service
    echo "deploy_restarted=$(date -Iseconds)" > "$STATE_FILE"
    exit 0
fi

# 2. Check that the tunnel URL exists.
URL=$(cat "$URL_FILE" 2>/dev/null)
if [ -z "$URL" ]; then
    log "⚠️  no URL in $URL_FILE -> restart tunnel"
    sudo -n systemctl restart cloudflared_tunnel.service
    echo "tunnel_restarted=$(date -Iseconds)" > "$STATE_FILE"
    exit 0
fi

# 3. Test ping through the external tunnel.
TUNNEL_CODE=$(http_code "$URL/ping")
if ! is_alive "$TUNNEL_CODE"; then
    log "⚠️  tunnel KO (HTTP=$TUNNEL_CODE) sur $URL → restart"
    sudo -n systemctl restart cloudflared_tunnel.service
    echo "tunnel_restarted=$(date -Iseconds)" > "$STATE_FILE"
fi

# 4. Truncate the log if it exceeds 200 KB.
SIZE=$(stat -c %s "$LOG" 2>/dev/null || echo 0)
if [ "$SIZE" -gt 204800 ]; then
    tail -500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

exit 0
