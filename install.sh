#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# assistant home — Interactive installation
# ═══════════════════════════════════════════════════════════════════
# Usage  : ./install.sh [--no-interactive] [--from-env]
# Requires: Python 3.10+, curl, jq
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────
# Using $'...' so ANSI sequences are interpreted at assignment time —
# they work correctly with cat, printf, echo.
BLUE=$'\033[1;34m'; GREEN=$'\033[1;32m'; YELLOW=$'\033[1;33m'
RED=$'\033[1;31m'; NC=$'\033[0m'; BOLD=$'\033[1m'

info()  { printf '%sℹ %s%s\n' "$BLUE" "$NC" "$*"; }
ok()    { printf '%s✓ %s%s\n' "$GREEN" "$NC" "$*"; }
warn()  { printf '%s⚠ %s%s\n' "$YELLOW" "$NC" "$*"; }
fail()  { printf '%s✗ %s%s\n' "$RED" "$NC" "$*" >&2; exit 1; }
title() { printf '\n%s═══ %s ═══%s\n' "$BOLD" "$*" "$NC"; }

# ── Directories ───────────────────────────────────────────────────────
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${INSTALL_DIR}/config.json"
ENV_FILE="${INSTALL_DIR}/.env"

# ── Flags ─────────────────────────────────────────────────────────────
NON_INTERACTIVE=0
FROM_ENV=0
for arg in "$@"; do
    case "$arg" in
        --no-interactive) NON_INTERACTIVE=1 ;;
        --from-env)        FROM_ENV=1 ;;
        --help|-h)
            sed -n '3,8p' "$0"; exit 0 ;;
    esac
done

# ═══════════════════════════════════════════════════════════════════
# 1. System prerequisites
# ═══════════════════════════════════════════════════════════════════
title "Checking prerequisites"

check_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        fail "Missing command: $1 — install it with: ${2:-$1}"
    fi
    ok "$1 found: $(command -v "$1")"
}

check_cmd python3 "apt install python3"
check_cmd pip3    "apt install python3-pip"
check_cmd curl    "apt install curl"

# Python >= 3.10
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    fail "Python $PY_VER detected. Python 3.10 or higher is required."
fi
ok "Python $PY_VER"

# jq recommended (for beta mode) but not blocking
if ! command -v jq >/dev/null 2>&1; then
    warn "jq not found (optional, useful for beta mode). Install it: apt install jq"
fi

# ═══════════════════════════════════════════════════════════════════
# 2. Python dependency installation
# ═══════════════════════════════════════════════════════════════════
title "Installing Python dependencies"

if [ -f "${INSTALL_DIR}/requirements.txt" ]; then
    info "Installing from requirements.txt..."
    pip3 install --user --upgrade -r "${INSTALL_DIR}/requirements.txt" 2>&1 | grep -E "Successfully|already|error" || true
    ok "Dependencies installed"
else
    warn "requirements.txt not found, manual installation..."
    pip3 install --user --upgrade anthropic openai requests matplotlib
fi

# ═══════════════════════════════════════════════════════════════════
# 3. Credential collection
# ═══════════════════════════════════════════════════════════════════
title "Configuration"

# If config.json already exists, ask for confirmation
if [ -f "$CONFIG_FILE" ]; then
    if [ "$NON_INTERACTIVE" -eq 1 ]; then
        info "config.json already exists (no-interactive mode), no changes"
        exit 0
    fi
    warn "config.json already exists."
    read -r -p "Overwrite? [y/N] " overwrite
    [ "${overwrite,,}" = "y" ] || { info "Keeping existing config.json"; exit 0; }
fi

# --from-env mode: read from .env
if [ "$FROM_ENV" -eq 1 ]; then
    [ -f "$ENV_FILE" ] || fail "$ENV_FILE not found (--from-env mode)"
    info "Loading from $ENV_FILE..."
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
fi

# Function to ask for a value with a default
ask() {
    local varname="$1" prompt="$2" default="${3:-}" secret="${4:-0}"
    local current="${!varname:-$default}"
    local value=""
    if [ "$NON_INTERACTIVE" -eq 1 ] || [ "$FROM_ENV" -eq 1 ]; then
        value="$current"
    elif [ "$secret" = "1" ]; then
        read -r -s -p "  $prompt ${current:+[masked, Enter to keep] }: " value
        echo
        [ -z "$value" ] && value="$current"
    else
        read -r -p "  $prompt${current:+ [$current]}: " value
        [ -z "$value" ] && value="$current"
    fi
    printf -v "$varname" '%s' "$value"
}

# ─── REQUIRED ───
echo
info "REQUIRED credentials (see README to obtain them)"

ask TELEGRAM_TOKEN     "Telegram Bot Token (via @BotFather)" "${TELEGRAM_TOKEN:-}" 1
[ -n "$TELEGRAM_TOKEN" ] || fail "TELEGRAM_TOKEN is empty"

ask HA_URL             "Home Assistant URL"                 "${HA_URL:-http://192.168.1.XX:8123}"
[ -n "$HA_URL" ] || fail "HA_URL is empty"

ask HA_TOKEN           "HA Long-Lived Token"                "${HA_TOKEN:-}" 1
[ -n "$HA_TOKEN" ] || fail "HA_TOKEN is empty"

ask LLM_PROVIDER       "AI provider (anthropic|openai|openrouter|ollama|lmstudio)" "${LLM_PROVIDER:-anthropic}"
case "$LLM_PROVIDER" in
    anthropic|openai|openrouter|ollama|lmstudio) : ;;
    *) fail "Unsupported LLM_PROVIDER: $LLM_PROVIDER" ;;
esac

ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
OPENAI_API_KEY="${OPENAI_API_KEY:-}"
OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}"
OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
LMSTUDIO_HOST="${LMSTUDIO_HOST:-http://localhost:1234}"
LLM_MODEL="${LLM_MODEL:-}"
LLM_MODEL_STRONG="${LLM_MODEL_STRONG:-}"

case "$LLM_PROVIDER" in
    anthropic)
        ask ANTHROPIC_API_KEY "Anthropic API Key" "$ANTHROPIC_API_KEY" 1
        [ -n "$ANTHROPIC_API_KEY" ] || fail "ANTHROPIC_API_KEY is empty"
        ;;
    openai)
        ask OPENAI_API_KEY "OpenAI API Key" "$OPENAI_API_KEY" 1
        [ -n "$OPENAI_API_KEY" ] || fail "OPENAI_API_KEY is empty"
        ;;
    openrouter)
        ask OPENROUTER_API_KEY "OpenRouter API Key" "$OPENROUTER_API_KEY" 1
        [ -n "$OPENROUTER_API_KEY" ] || fail "OPENROUTER_API_KEY is empty"
        ;;
    ollama)
        ask OLLAMA_HOST "Ollama host" "$OLLAMA_HOST"
        ;;
    lmstudio)
        ask LMSTUDIO_HOST "LM Studio host" "$LMSTUDIO_HOST"
        ;;
esac

# ─── OPTIONAL ───
echo
info "Options (Enter for default values)"

ask LLM_MODEL                    "AI model override (blank = provider default)" "$LLM_MODEL"
ask LLM_MODEL_STRONG             "Strong AI model override (blank = provider default)" "$LLM_MODEL_STRONG"
ask LLM_MONTHLY_BUDGET_USD       "Internal monthly AI budget USD (0 = off)" "${LLM_MONTHLY_BUDGET_USD:-${ANTHROPIC_MONTHLY_BUDGET_USD:-0}}"
ask SMS_METHOD                   "Security code method (free_mobile|ha_notify|email)" "${SMS_METHOD:-ha_notify}"

FREE_MOBILE_USER="${FREE_MOBILE_USER:-}"
FREE_MOBILE_PASS="${FREE_MOBILE_PASS:-}"
SMTP_HOST="${SMTP_HOST:-smtp.gmail.com}"
SMTP_PORT="${SMTP_PORT:-587}"
SMTP_USER="${SMTP_USER:-}"
SMTP_PASS="${SMTP_PASS:-}"
MAIL_DEST="${MAIL_DEST:-}"

case "$SMS_METHOD" in
    free_mobile)
        ask FREE_MOBILE_USER "Free Mobile username" "$FREE_MOBILE_USER"
        ask FREE_MOBILE_PASS "Free Mobile API key"  "$FREE_MOBILE_PASS" 1
        ;;
    email)
        ask SMTP_HOST "SMTP host"     "$SMTP_HOST"
        ask SMTP_PORT "SMTP port"     "$SMTP_PORT"
        ask SMTP_USER "SMTP user"     "$SMTP_USER"
        ask SMTP_PASS "SMTP password" "$SMTP_PASS" 1
        ask MAIL_DEST "Recipient email" "$MAIL_DEST"
        ;;
    ha_notify) : ;;
    *) warn "Unknown SMS_METHOD: $SMS_METHOD — using ha_notify"; SMS_METHOD=ha_notify ;;
esac

# ═══════════════════════════════════════════════════════════════════
# 4. config.json generation
# ═══════════════════════════════════════════════════════════════════
title "Generating config.json"

export TELEGRAM_TOKEN HA_URL HA_TOKEN LLM_PROVIDER ANTHROPIC_API_KEY OPENAI_API_KEY OPENROUTER_API_KEY OLLAMA_HOST LMSTUDIO_HOST LLM_MODEL LLM_MODEL_STRONG
export FREE_MOBILE_USER FREE_MOBILE_PASS SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASS MAIL_DEST
export SMS_METHOD LLM_MONTHLY_BUDGET_USD

python3 - <<PYEOF
import json, os, secrets
cfg = {
    "telegram_token":               os.environ.get("TELEGRAM_TOKEN",""),
    "telegram_chat_id":             "",
    "ha_url":                       os.environ.get("HA_URL",""),
    "ha_token":                     os.environ.get("HA_TOKEN",""),
    "llm_provider":                 os.environ.get("LLM_PROVIDER","anthropic"),
    "anthropic_api_key":            os.environ.get("ANTHROPIC_API_KEY",""),
    "openai_api_key":               os.environ.get("OPENAI_API_KEY",""),
    "openrouter_api_key":           os.environ.get("OPENROUTER_API_KEY",""),
    "llm_model":                    os.environ.get("LLM_MODEL",""),
    "llm_model_strong":             os.environ.get("LLM_MODEL_STRONG",""),
    "ollama_host":                  os.environ.get("OLLAMA_HOST","http://localhost:11434"),
    "lmstudio_host":                os.environ.get("LMSTUDIO_HOST","http://localhost:1234"),
    "free_mobile_user":             os.environ.get("FREE_MOBILE_USER",""),
    "free_mobile_pass":             os.environ.get("FREE_MOBILE_PASS",""),
    "smtp_host":                    os.environ.get("SMTP_HOST",""),
    "smtp_port":                    int(os.environ.get("SMTP_PORT","587") or 587),
    "smtp_user":                    os.environ.get("SMTP_USER",""),
    "smtp_pass":                    os.environ.get("SMTP_PASS",""),
    "email_dest":                    os.environ.get("MAIL_DEST",""),
    "sms_method":                   os.environ.get("SMS_METHOD","ha_notify"),
    "poll_interval_sec":            2,
    "audit_interval_sec":           1800,
    "llm_monthly_budget_usd":       int(os.environ.get("LLM_MONTHLY_BUDGET_USD","0") or 0),
    "deploy_secret":                secrets.token_hex(32),
}
with open("${CONFIG_FILE}", "w") as f:
    json.dump(cfg, f, indent=2)
os.chmod("${CONFIG_FILE}", 0o600)
print(f"✓ config.json written ({len(cfg)} keys, permissions 600)")
PYEOF

# ═══════════════════════════════════════════════════════════════════
# 5. Credential test
# ═══════════════════════════════════════════════════════════════════
title "Testing credentials"

# HA
info "Testing Home Assistant..."
if curl -sf -m 10 -H "Authorization: Bearer $HA_TOKEN" "$HA_URL/api/" >/dev/null 2>&1; then
    ok "Home Assistant: connection OK"
else
    warn "Home Assistant unreachable or token invalid (you can correct it in config.json)"
fi

# Telegram
info "Testing Telegram..."
if curl -sf -m 10 "https://api.telegram.org/bot$TELEGRAM_TOKEN/getMe" >/dev/null 2>&1; then
    ok "Telegram: valid bot"
else
    warn "Invalid Telegram token (you can correct it in config.json)"
fi

# ═══════════════════════════════════════════════════════════════════
# 6. Next steps
# ═══════════════════════════════════════════════════════════════════
title "Installation complete"

cat <<INFO

  ${BOLD}Next steps:${NC}

  1. Start the bot:
     ${BLUE}python3 assistant.py${NC}

  2. Send any message to your Telegram bot
     → chat_id is detected automatically on first message

  3. The bot guides you through the rest (appliance questionnaire, rate, etc.)

  ${BOLD}Deploy as a service:${NC}
    ${BLUE}./scripts/install_systemd.sh${NC}       Linux/Pi/VM
    ${BLUE}docker compose up -d${NC}                Docker
    See docs/INSTALL.md for HA App

  ${BOLD}Help:${NC}  /help in Telegram     │     ${BOLD}Docs:${NC}  docs/

INFO
