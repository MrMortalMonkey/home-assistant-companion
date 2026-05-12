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
| `anthropic_api_key` | str | ✅ | API key from [console.anthropic.com](https://console.anthropic.com) |
| `anthropic_monthly_budget_usd` | int | ✅ | Monthly budget in USD (alerts at 50/80/100%) |
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

### 3. Anthropic API Key

1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Create an account → add a card (credit required)
3. **API Keys → Create Key**
4. Copy the key (starts with `sk-ant-...`)

**Budget:** normal usage costs $5-10/month. Set `anthropic_monthly_budget_usd: 10` to start, adjust as needed.

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
  "anthropic_api_key": "sk-ant-api03-xxxxx...",
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
  "anthropic_monthly_budget_usd": 10,
  "deploy_secret": "3f8a9b2c7e1d4f5a6b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a"
}
```

## Changing config after installation

1. Edit `config.json` directly
2. Restart AI Assistant:
   - HA Add-on: **Restart** button in the interface
   - Docker: `docker compose restart assistant`
   - Native Linux: `sudo systemctl restart assistant.service`
   - Manual: Ctrl+C then `python3 assistant.py`

## Configuration rules

- **Never commit `config.json` to git** (it's in `.gitignore` by default)
- `deploy_secret` is auto-generated — leave it alone unless you're deliberately regenerating it
- `telegram_chat_id` fills itself on first message received
- If you change Telegram bots, start from a new `config.json` (the channel re-locks)
