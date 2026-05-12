#!/bin/bash
# Wrapper systemd-friendly: execute cloudflared, capture URL, publie sur ntfy.
set -u
TOPIC="assistant-deploy-8501-secret"
URL_FILE="/home/lolufe/assistant/tunnel_url.txt"

TMP_LOG=$(mktemp)
cloudflared tunnel --url http://localhost:8501 > "$TMP_LOG" 2>&1 &
CFD_PID=$!

# Clean shutdown on termination (SIGTERM of systemd)
cleanup() {
    kill -TERM "$CFD_PID" 2>/dev/null || true
    wait "$CFD_PID" 2>/dev/null
    rm -f "$TMP_LOG"
    exit 0
}
trap cleanup TERM INT

tail -f "$TMP_LOG" &
TAIL_PID=$!

# Wait URL (max 30s)
URL=""
for i in $(seq 1 60); do
    URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TMP_LOG" 2>/dev/null | head -1)
    if [ -n "$URL" ]; then
        echo "$URL" > "$URL_FILE"
        echo ">>> URL_PUBLISHED: $URL"
        curl -s -m 10 -d "$URL" "https://ntfy.sh/$TOPIC" >/dev/null
        break
    fi
    if ! kill -0 "$CFD_PID" 2>/dev/null; then
        echo ">>> ERROR: cloudflared is dead before publishing of URL"
        kill "$TAIL_PID" 2>/dev/null
        rm -f "$TMP_LOG"
        exit 1
    fi
    sleep 0.5
done

if [ -z "$URL" ]; then
    echo ">>> ERROR: not of URL after 30s"
fi

# Stay alive while cloudflared is running
wait "$CFD_PID"
EXIT=$?
kill "$TAIL_PID" 2>/dev/null
rm -f "$TMP_LOG"
exit "$EXIT"
