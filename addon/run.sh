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

# Required
TELEGRAM_TOKEN=$(bashio::config 'telegram_token')
LLM_PROVIDER=$(bashio::config 'llm_provider')
SMS_METHOD=$(bashio::config 'sms_method')

# AI provider credentials
ANTHROPIC_KEY=$(bashio::config 'anthropic_api_key')
OPENAI_KEY=$(bashio::config 'openai_api_key')
OPENAI_BASE_URL=$(bashio::config 'openai_base_url')
OPENAI_ORG_ID=$(bashio::config 'openai_organization_id')
OPENAI_PROJECT_ID=$(bashio::config 'openai_project_id')
OPENROUTER_KEY=$(bashio::config 'openrouter_api_key')
OLLAMA_HOST=$(bashio::config 'ollama_host')
LMSTUDIO_HOST=$(bashio::config 'lmstudio_host')

# Model overrides
LLM_MODEL=$(bashio::config 'llm_model')
LLM_MODEL_STRONG=$(bashio::config 'llm_model_strong')

# Features
ENABLE_BRIEFING=$(bashio::config 'enable_morning_briefing')
ENABLE_EVENING=$(bashio::config 'enable_evening_summary')
ENABLE_APPLIANCE=$(bashio::config 'enable_appliance_detection')

# Regional
TIMEZONE=$(bashio::config 'timezone')
COUNTRY_CODE=$(bashio::config 'country_code')
ELECTRICITY_RATE=$(bashio::config 'electricity_rate_kwh')
CURRENCY=$(bashio::config 'currency')

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

# Preserve runtime-only values learned after install (not exposed in HA App UI)
CHAT_ID=""
CURRENT_TOKEN=""
DEPLOY_SECRET=""
FREE_MOBILE_USER=""
FREE_MOBILE_PASS=""
SMTP_HOST=""
SMTP_PORT="587"
SMTP_USER=""
SMTP_PASS=""
EMAIL_DEST=""
POLL_INTERVAL="2"
AUDIT_INTERVAL="1800"

if [ -f "${CONFIG_FILE}" ]; then
    CURRENT_TOKEN=$(jq -r '.telegram_token // ""' "${CONFIG_FILE}" 2>/dev/null || echo "")
    CHAT_ID=$(jq -r '.telegram_chat_id // ""' "${CONFIG_FILE}" 2>/dev/null || echo "")
    DEPLOY_SECRET=$(jq -r '.deploy_secret // ""' "${CONFIG_FILE}" 2>/dev/null || echo "")
    FREE_MOBILE_USER=$(jq -r '.free_mobile_user // ""' "${CONFIG_FILE}" 2>/dev/null || echo "")
    FREE_MOBILE_PASS=$(jq -r '.free_mobile_pass // ""' "${CONFIG_FILE}" 2>/dev/null || echo "")
    SMTP_HOST=$(jq -r '.smtp_host // ""' "${CONFIG_FILE}" 2>/dev/null || echo "")
    SMTP_PORT=$(jq -r '.smtp_port // 587' "${CONFIG_FILE}" 2>/dev/null || echo "587")
    SMTP_USER=$(jq -r '.smtp_user // ""' "${CONFIG_FILE}" 2>/dev/null || echo "")
    SMTP_PASS=$(jq -r '.smtp_pass // ""' "${CONFIG_FILE}" 2>/dev/null || echo "")
    EMAIL_DEST=$(jq -r '.email_dest // ""' "${CONFIG_FILE}" 2>/dev/null || echo "")
    POLL_INTERVAL=$(jq -r '.poll_interval_sec // 2' "${CONFIG_FILE}" 2>/dev/null || echo "2")
    AUDIT_INTERVAL=$(jq -r '.audit_interval_sec // 1800' "${CONFIG_FILE}" 2>/dev/null || echo "1800")
fi

if [ -n "${CURRENT_TOKEN}" ] && [ "${CURRENT_TOKEN}" != "${TELEGRAM_TOKEN}" ]; then
    CHAT_ID=""
fi

if [ -z "${DEPLOY_SECRET}" ]; then
    DEPLOY_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
fi

bashio::log.info "Writing config.json..."
jq -n \
    --arg telegram          "${TELEGRAM_TOKEN}" \
    --arg chat_id           "${CHAT_ID}" \
    --arg haurl             "${HA_URL}" \
    --arg hatoken           "${HA_TOKEN}" \
    --arg provider          "${LLM_PROVIDER}" \
    --arg anth              "${ANTHROPIC_KEY}" \
    --arg openai            "${OPENAI_KEY}" \
    --arg openai_base_url   "${OPENAI_BASE_URL:-https://api.openai.com/v1}" \
    --arg openai_org        "${OPENAI_ORG_ID}" \
    --arg openai_project    "${OPENAI_PROJECT_ID}" \
    --arg router            "${OPENROUTER_KEY}" \
    --arg model             "${LLM_MODEL}" \
    --arg strong            "${LLM_MODEL_STRONG}" \
    --arg ollama            "${OLLAMA_HOST}" \
    --arg lmstudio          "${LMSTUDIO_HOST}" \
    --arg sms               "${SMS_METHOD}" \
    --arg secret            "${DEPLOY_SECRET}" \
    --arg free_user         "${FREE_MOBILE_USER}" \
    --arg free_pass         "${FREE_MOBILE_PASS}" \
    --arg smtp_host         "${SMTP_HOST}" \
    --arg smtp_port         "${SMTP_PORT}" \
    --arg smtp_user         "${SMTP_USER}" \
    --arg smtp_pass         "${SMTP_PASS}" \
    --arg email_dest        "${EMAIL_DEST}" \
    --arg poll_interval     "${POLL_INTERVAL}" \
    --arg audit_interval    "${AUDIT_INTERVAL}" \
    --argjson enable_briefing  "${ENABLE_BRIEFING:-true}" \
    --argjson enable_evening   "${ENABLE_EVENING:-true}" \
    --argjson enable_appliance "${ENABLE_APPLIANCE:-true}" \
    --arg timezone          "${TIMEZONE}" \
    --arg country_code      "${COUNTRY_CODE:-us}" \
    --arg electricity_rate  "${ELECTRICITY_RATE:-0.15}" \
    --arg currency          "${CURRENCY:-$}" \
    '{
        telegram_token:         $telegram,
        telegram_chat_id:       $chat_id,
        ha_url:                 $haurl,
        ha_token:               $hatoken,
        llm_provider:           $provider,
        anthropic_api_key:      $anth,
        openai_api_key:         $openai,
        openai_base_url:        $openai_base_url,
        openai_organization_id: $openai_org,
        openai_project_id:      $openai_project,
        openrouter_api_key:     $router,
        llm_model:              $model,
        llm_model_strong:       $strong,
        ollama_host:            $ollama,
        lmstudio_host:          $lmstudio,
        sms_method:             $sms,
        poll_interval_sec:      ($poll_interval | tonumber? // 2),
        audit_interval_sec:     ($audit_interval | tonumber? // 1800),
        deploy_secret:          $secret,
        free_mobile_user:       $free_user,
        free_mobile_pass:       $free_pass,
        smtp_host:              $smtp_host,
        smtp_port:              ($smtp_port | tonumber? // 587),
        smtp_user:              $smtp_user,
        smtp_pass:              $smtp_pass,
        email_dest:             $email_dest,
        enable_morning_briefing: $enable_briefing,
        enable_evening_summary:  $enable_evening,
        enable_appliance_detection: $enable_appliance,
        timezone:               $timezone,
        country_code:           $country_code,
        electricity_rate_kwh:   ($electricity_rate | tonumber? // 0.15),
        currency:               $currency
    }' > "${CONFIG_FILE}"
chmod 600 "${CONFIG_FILE}"
bashio::log.info "config.json written (HA URL: ${HA_URL})"

# Link config.json and memory.db from /config to /app (persistence)
ln -sf "${CONFIG_FILE}" "${APP_DIR}/config.json"
if [ -f "${DB_FILE}" ]; then
    ln -sf "${DB_FILE}" "${APP_DIR}/memory.db"
fi

cd "${APP_DIR}"
bashio::log.info "Starting Home Assistant AI Companion..."
exec python3 -u assistant.py
