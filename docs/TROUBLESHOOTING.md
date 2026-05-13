# 🔧 Troubleshooting — Home Assistant AI Companion

## The bot doesn't respond on Telegram

**Checks:**

1. Is the service running?
   - HA App: **Log** tab of the app
   - Docker: `docker compose logs --tail=100`
   - Linux: `sudo systemctl status assistant.service`

2. Is the Telegram token valid?
   ```bash
   curl "https://api.telegram.org/bot<YOUR_TOKEN>/getMe"
   ```
   Should return `{"ok":true,"result":{...}}`. Otherwise the token is wrong.

3. Did you send the **first message** to the bot?
   The bot detects your `chat_id` only after your first message. Send "hello".

4. Is the channel locked? (6-digit code not entered)
   Wait for the code via SMS / HA notification / email and type it in Telegram.

## The bot doesn't see my Home Assistant devices

**Checks:**

1. Is the HA URL reachable from the bot's machine?
   ```bash
   curl -H "Authorization: Bearer <HA_TOKEN>" http://<HA_IP>:8123/api/
   ```
   Should return `{"message":"API running."}`.

2. Does the HA token have proper access?
   ```bash
   curl -H "Authorization: Bearer <HA_TOKEN>" http://<HA_IP>:8123/api/states | head
   ```

3. Manually re-trigger the scan from Telegram: `/scan`

## Error "Rate limit exceeded" from your AI provider

Your selected AI provider key has reached its limit. Options:

- Wait a few minutes
- Increase the quota in your provider console
- Adjust `llm_monthly_budget_usd` in `config.json`

## The bot crashes at startup

**Error "ModuleNotFoundError"** — missing dependencies:
```bash
pip3 install --user --upgrade -r requirements.txt
```

**Error "FileNotFoundError: config.json"** — re-run the wizard:
```bash
./install.sh
```

**Error "sqlite3.DatabaseError"** — corrupted database. Backup + reset:
```bash
mv memory.db memory.db.broken
# The bot will create a new DB on startup.
```

## The bot sends too many (or too few) notifications

Adjust from Telegram:

- `/commands` — see all commands
- `/budget` — check API consumption
- `/debug` — thread status

**Temporarily disable an alert:** `/watches` to see active alerts, `/watches delete <id>` to remove one.

## Appliance cycles aren't detected

1. Check that a plug is associated with a machine: `/appliances`
2. If not, reconfigure: `/appliances reset` → the questionnaire restarts
3. Check that the plug has a power sensor in Watts: `/monitoring`

## Savings aren't showing

`/roi` requires several complete cycles to calibrate. Expect 1 week before seeing first figures.

## Beta mode: `/restart_self` doesn't work

Read [docs/BETA_CHANNEL.md](BETA_CHANNEL.md) — deploy_server must be enabled (opt-in) and the Cloudflare tunnel must be running.

## Starting over from scratch

To start clean, preserving your credentials:

```bash
# Backup
cp config.json config.json.backup
cp memory.db memory.db.backup

# Keyan
rm memory.db
rm -rf __pycache__

# Restart
sudo systemctl restart assistant.service   # (or docker compose restart)
```

## Getting help

1. **`/problem <description>`** in Telegram — the configured AI model diagnoses your problem
2. **Detailed logs**:
   - HA App: Log tab
   - Docker: `docker compose logs --tail=200 assistant`
   - Linux: `sudo journalctl -u assistant.service -n 200`
3. **GitHub Issues**: https://github.com/MrMortalMonkey/home-assistant-companion/issues
