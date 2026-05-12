# Home Assistant AI Companion

## Configuration

After installing the add-on, configure these 2 parameters in the **Configuration** tab:

1. **telegram_token** — Your Telegram bot token (created via @BotFather)
2. **anthropic_api_key** — Anthropic API key (console.anthropic.com)

The Home Assistant URL and token are configured **automatically** by the Supervisor.

## First launch

1. Start the add-on
2. Send a message to your Telegram bot → chat_id is detected automatically
3. The bot asks a few questions: security method, electricity rate, appliances on plugs
4. That's it — the assistant monitors your home

## Commands

Type `/help` in Telegram to see all available commands.

## Support

- GitHub Issues for bugs
- `/problem <description>` for the AI to diagnose and propose a fix
