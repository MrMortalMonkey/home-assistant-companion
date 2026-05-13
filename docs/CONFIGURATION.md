# ⚙️ Configuration — Home Assistant AI Companion

All configuration is in `config.json`. This file is generated automatically by `./install.sh`, or can be edited by hand.

> ⚠️ `config.json` contains your secrets. It is created with `600` permissions (owner read only). **Never commit it to git.**

## Structure of `config.json`

| Key | Type | Required | Description |
|---|---|---|---|
| `telegram_token` | str | ✅ | Bot token via [@BotFather](https://t.me/BotFather) |
| `telegram_chat_id` | str | Auto | Detected on first message |
| `ha_url` | str | ✅ | Home Assistant URL (e.g. `http://192.168.1.76:8123`) |
| `ha_token` | str | ✅ | HA long-lived access token |
| `llm_provider` | str | ✅ | AI provider: `anthropic`, `openai`, `openrouter`, `ollama`, or `lmstudio` |
| `anthropic_api_key` | str | If Anthropic | Anthropic API key |
| `openai_api_key` | str | If OpenAI | OpenAI API key |
| `openai_base_url` | str | Optional | OpenAI-compatible API base URL. Defaults to `https://api.openai.com/v1`. |
| `openai_organization_id` | str | Optional | OpenAI organization header value, if your account requires it. |
| `openai_project_id` | str | Optional | OpenAI project header value, if you want requests scoped to a project. |
| `openrouter_api_key` | str | If OpenRouter | OpenRouter API key |
| `llm_model` | str | Optional | Model ID for normal requests. Leave blank to use the provider default. |
| `llm_model_strong` | str | Optional | Model ID for heavier troubleshooting/auto-fix requests. Leave blank to use the provider default. |
| `ollama_host` | str | If Ollama | Ollama base URL, usually `http://localhost:11434` |
| `lmstudio_host` | str | If LM Studio | LM Studio base URL, usually `http://localhost:1234` |
| `llm_monthly_budget_usd` | int | Optional | Internal monthly AI budget cap in USD. `0` disables the internal cap. |
| `sms_method` | str | ✅ | `free_mobile` \| `ha_notify` \| `email` |
| `free_mobile_user` | str | If `free_mobile` | Free Mobile username |
| `free_mobile_pass` | str | If `free_mobile` | Free Mobile API key |
| `smtp_host` | str | If `email` | SMTP server (e.g. `smtp.gmail.com`) |
| `smtp_port` | int | If `email` | SMTP port (587 for TLS) |
| `smtp_user` | str | If `email` | SMTP username |
| `smtp_pass` | str | If `email` | Password (or Gmail App Password) |
| `email_dest` | str | If `email` | Recipient email address |
| `poll_interval_sec` | int | ✅ | Telegram polling interval (default: 2) |
| `audit_interval_sec` | int | ✅ | Full audit interval in seconds (default: 1800) |
| `deploy_secret` | str | ✅ | 64-char HMAC secret (auto-generated) — **never share** |

## Getting credentials

### 1. Telegram Bot Token

1. Open Telegram, search for [@BotFather](https://t.me/BotFather)
2. `/newbot` → choose a name → choose a username ending in `_bot`
3. BotFather gives you a token in the form `123456789:ABCdefGhIJKlmnopQRStuvwxyz`
4. Keep it secret — it allows full control of your bot

### 2. Home Assistant — URL and token

**URL:** the URL of your HA accessible from the machine running AI Assistant.
- Local network: `http://192.168.1.XX:8123`
- Via DuckDNS / Nabu Casa: `https://xxx.duckdns.org`

**Token:**
1. In HA, click your avatar at the bottom left
2. **Security** tab → **Long-lived access tokens**
3. Click **Create token**
4. Give it a name (e.g. `AI Assistant`) → copy the displayed token
5. ⚠️ This token is shown only once — keep it safe

### 3. AI provider

Choose the provider you want in `llm_provider`, then fill in the matching key or endpoint:

- `anthropic`: set `anthropic_api_key`
- `openai`: set `openai_api_key`
- `openrouter`: set `openrouter_api_key`
- `ollama`: set `ollama_host`
- `lmstudio`: set `lmstudio_host`

OpenAI options:

- `openai_base_url`: leave as `https://api.openai.com/v1` for the normal OpenAI API.
- `openai_organization_id`: optional organization ID for accounts that require the `OpenAI-Organization` header.
- `openai_project_id`: optional project ID for accounts that use the `OpenAI-Project` header.
- OAuth is not used by this app. It runs server-side inside Home Assistant and authenticates to model APIs with provider API keys.

Optional model overrides:

- `llm_model`: normal chat/control model
- `llm_model_strong`: heavier troubleshooting and auto-fix model

Home Assistant's setup form is static, so the app uses model text fields instead of dynamic provider dropdowns.

Provider examples:

- Anthropic: `claude-haiku-4-5-20251001` for normal use, `claude-sonnet-4-6` for stronger reasoning.
- OpenAI: use a model ID available to your key, or list available IDs with the OpenAI Models API.
- OpenRouter: enter IDs exactly as OpenRouter lists them, such as `openai/gpt-4o-mini` or `anthropic/claude-3.5-haiku`.
- Ollama and LM Studio: enter the locally installed model name exposed by that server.

**Budget:** hosted model usage often costs $5-10/month for light home automation usage. Prefer provider-side usage limits when available, such as an OpenRouter key limit. If you also want a local software cap, set `llm_monthly_budget_usd`; use `0` to disable the internal cap.

### 4. Security method (6-digit code at startup)

At startup, AI Assistant sends a 6-digit code to verify it's you accessing the bot.

**Three possible methods:**

- **`ha_notify`** (recommended): uses Home Assistant notifications (mobile app). Zero additional configuration.
- **`free_mobile`**: free SMS if you have a Free Mobile plan. Get your credentials in the Free subscriber area → My options → SMS notifications.
- **`email`**: email via SMTP. For Gmail, create an [App Password](https://myaccount.google.com/apppasswords).

## Full example

```json
{
  "telegram_token": "123456789:ABCdefGhIJKlmnopQRStuvwxyz",
  "telegram_chat_id": "",
  "ha_url": "http://192.168.1.76:8123",
  "ha_token": "eyJhbGciOiJIUzI1...",
  "llm_provider": "anthropic",
  "anthropic_api_key": "sk-ant-api03-xxxxx...",
  "openai_api_key": "",
  "openai_base_url": "https://api.openai.com/v1",
  "openai_organization_id": "",
  "openai_project_id": "",
  "openrouter_api_key": "",
  "llm_model": "",
  "llm_model_strong": "",
  "ollama_host": "http://localhost:11434",
  "lmstudio_host": "http://localhost:1234",
  "sms_method": "ha_notify",
  "free_mobile_user": "",
  "free_mobile_pass": "",
  "smtp_host": "",
  "smtp_port": 587,
  "smtp_user": "",
  "smtp_pass": "",
  "email_dest": "",
  "poll_interval_sec": 2,
  "audit_interval_sec": 1800,
  "llm_monthly_budget_usd": 0,
  "deploy_secret": "3f8a9b2c7e1d4f5a6b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a"
}
```

## Changing config after installation

1. Edit `config.json` directly
2. Restart AI Assistant:
   - HA App: **Restart** button in the interface
   - Docker: `docker compose restart assistant`
   - Native Linux: `sudo systemctl restart assistant.service`
   - Manual: Ctrl+C then `python3 assistant.py`

## Configuration rules

- **Never commit `config.json` to git** (it's in `.gitignore` by default)
- `deploy_secret` is auto-generated — leave it alone unless you're deliberately regenerating it
- `telegram_chat_id` fills itself on first message received
- If you change Telegram bots, start from a new `config.json` (the channel re-locks)
