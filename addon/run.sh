#!/usr/bin/with-contenv bashio
# Supervisor provides the HA URL and token automatically.
# The user configures telegram_token, an AI provider, and the matching credentials.

set -e

APP_DIR="/app"
CONFIG_DIR="/config/assistant"
CONFIG_FILE="${CONFIG_DIR}/config.json"
DB_FILE="${CONFIG_DIR}/memory.db"

mkdir -p "${CONFIG_DIR}"

# HA credentials provided by Supervisor
HA_URL="http://supervisor/core"
HA_TOKEN="${SUPERVISOR_TOKEN}"

# User options
TELEGRAM_TOKEN=$(bashio::config 'telegram_token')
LLM_PROVIDER=$(bashio::config 'llm_provider')
ANTHROPIC_KEY=$(bashio::config 'anthropic_api_key')
OPENAI_KEY=$(bashio::config 'openai_api_key')
OPENROUTER_KEY=$(bashio::config 'openrouter_api_key')
OLLAMA_HOST=$(bashio::config 'ollama_host')
LMSTUDIO_HOST=$(bashio::config 'lmstudio_host')
SMS_METHOD=$(bashio::config 'sms_method')
ENABLE_DEPLOY=$(bashio::config 'enable_deploy_server')

# Minimal validation
if [ -z "${TELEGRAM_TOKEN}" ]; then
    bashio::log.fatal "telegram_token is required"
    exit 1
fi

case "${LLM_PROVIDER}" in
    anthropic)
        [ -n "${ANTHROPIC_KEY}" ] || { bashio::log.fatal "anthropic_api_key is required for llm_provider=anthropic"; exit 1; }
        ;;
    openai)
        [ -n "${OPENAI_KEY}" ] || { bashio::log.fatal "openai_api_key is required for llm_provider=openai"; exit 1; }
        ;;
    openrouter)
        [ -n "${OPENROUTER_KEY}" ] || { bashio::log.fatal "openrouter_api_key is required for llm_provider=openrouter"; exit 1; }
        ;;
    ollama|lmstudio)
        ;;
    *)
        bashio::log.fatal "Unsupported llm_provider: ${LLM_PROVIDER}"
        exit 1
        ;;
esac

# Generate config.json if missing or credentials changed
NEED_GEN=1
if [ -f "${CONFIG_FILE}" ]; then
    CURRENT_TOKEN=$(jq -r .telegram_token "${CONFIG_FILE}" 2>/dev/null || echo "")
    if [ "${CURRENT_TOKEN}" = "${TELEGRAM_TOKEN}" ]; then
        NEED_GEN=0
    fi
fi

if [ "${NEED_GEN}" = "1" ]; then
    bashio::log.info "Generating config.json..."
    # Unique HMAC secret for this installation
    DEPLOY_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

    jq -n \
        --arg telegram "${TELEGRAM_TOKEN}" \
        --arg haurl    "${HA_URL}" \
        --arg hatoken  "${HA_TOKEN}" \
        --arg provider "${LLM_PROVIDER}" \
        --arg anth     "${ANTHROPIC_KEY}" \
        --arg openai   "${OPENAI_KEY}" \
        --arg router   "${OPENROUTER_KEY}" \
        --arg ollama   "${OLLAMA_HOST}" \
        --arg lmstudio "${LMSTUDIO_HOST}" \
        --arg sms      "${SMS_METHOD}" \
        --arg secret   "${DEPLOY_SECRET}" \
        '{
            telegram_token: $telegram,
            telegram_chat_id: "",
            ha_url: $haurl,
            ha_token: $hatoken,
            llm_provider: $provider,
            anthropic_api_key: $anth,
            openai_api_key: $openai,
            openrouter_api_key: $router,
            ollama_host: $ollama,
            lmstudio_host: $lmstudio,
            sms_method: $sms,
            poll_interval_sec: 2,
            audit_interval_sec: 1800,
            deploy_secret: $secret,
            free_mobile_user: "",
            free_mobile_pass: "",
            smtp_host: "",
            smtp_port: 587,
            smtp_user: "",
            smtp_pass: "",
            email_dest: ""
        }' > "${CONFIG_FILE}"
    chmod 600 "${CONFIG_FILE}"
    bashio::log.info "config.json generated (HA URL: ${HA_URL})"
fi

# Link config.json and memory.db from /config to /app (persistence)
ln -sf "${CONFIG_FILE}" "${APP_DIR}/config.json"
if [ -f "${DB_FILE}" ]; then
    ln -sf "${DB_FILE}" "${APP_DIR}/memory.db"
fi

cd "${APP_DIR}"

# Deploy server (opt-in)
if [ "${ENABLE_DEPLOY}" = "true" ]; then
    bashio::log.warning "⚠️  Deploy server ENABLED (beta tester mode)"
    bashio::log.warning "    See docs/BETA_CHANNEL.md - exposes an HTTP port for remote patches"
    python3 -u deploy_server.py &
fi

# Main script
bashio::log.info "Starting Home Assistant AI Companion..."
exec python3 -u assistant.py
