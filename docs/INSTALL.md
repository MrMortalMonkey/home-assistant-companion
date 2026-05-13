# 📦 Installation Guide — Home Assistant AI Companion

Four installation methods supported. Choose according to your environment.

| Method | Difficulty | Auto-recovery | Updates |
|---|---|---|---|
| HA App | 🟢 Very easy | ✅ Supervisor | 1 click |
| Docker Compose | 🟢 Easy | ✅ `restart: unless-stopped` | `docker compose pull && up -d` |
| Native Linux | 🟡 Medium | ✅ systemd | `git pull && restart` |
| Manual | 🟡 Medium | ❌ | `git pull` |

---

## HA App

**Prerequisites:** Home Assistant OS or Home Assistant Supervised.

### Steps

1. In Home Assistant, go to **Settings → Apps**
2. Select **Install app**, then open the three-dot menu → **Repositories**
3. Add the URL: `https://github.com/MrMortalMonkey/home-assistant-companion`
4. Close, reload — find **Home Assistant AI Companion** in the list
5. Click **Install**
6. **Configuration** tab — fill in at minimum:
   - `telegram_token` (via [@BotFather](https://t.me/BotFather))
   - `llm_provider`
   - the matching provider API key or local endpoint
   - Leave `sms_method` as `ha_notify` (uses HA notifications)
7. Start the app
8. Send a message to your Telegram bot to complete setup

### Persistent data

- `config.json` and `memory.db` are stored in `/config/assistant/` on the HA side
- Logs are in the app's **Log** tab

### Update

**Update** button in the HA interface when a new version is published.

---

## Docker

**Prerequisites:** Docker + Docker Compose (NAS, Linux, Mac, Windows WSL...).

### Steps

```bash
# 1. Get the code
git clone https://github.com/MrMortalMonkey/home-assistant-companion.git
cd home-assistant-companion

# 2. Prepare the config
cp env.example .env
nano .env   # fill in TELEGRAM_TOKEN, HA_URL, HA_TOKEN, LLM_PROVIDER, and provider credentials

# 3. Generate config.json (option A: from .env)
./install.sh --from-env

# 3b. (option B: interactive, without .env)
./install.sh

# 4. Start
docker compose up -d

# 5. Follow logs
docker compose logs -f assistant
```

### Structure after installation

```
.
├── config.json          # your secrets (chmod 600)
├── .env                 # optional
├── data/
│   ├── memory.db        # persistent SQLite database
│   ├── assistant.log    # application logs
│   └── config.json      # read by the container
├── docker-compose.yml
└── Dockerfile
```

### Update

```bash
git pull
docker compose build     # if building locally
# or: docker compose pull  (if using the ghcr.io image)
docker compose up -d
```

### Stop / restart

```bash
docker compose restart assistant
docker compose stop
docker compose down      # removes the container (data stays in ./data)
```

---

## Native Linux

**Prerequisites:** Python 3.10+, Raspberry Pi OS / Ubuntu / Debian, sudo access.

### Steps

```bash
# 1. System dependencies
sudo apt update
sudo apt install -y python3 python3-pip git curl jq

# 2. Clone
git clone https://github.com/MrMortalMonkey/home-assistant-companion.git
cd home-assistant-companion

# 3. Installation wizard (dependencies + config.json)
./install.sh

# 4. Manual test
python3 assistant.py
# → Send a message to your Telegram bot to verify the connection
# → Ctrl+C to stop

# 5. Deploy as systemd service (auto-start at boot, auto-recovery)
sudo ./scripts/install_systemd.sh
```

### Service management

```bash
sudo systemctl status  assistant.service      # status
sudo systemctl restart assistant.service      # restart
sudo systemctl stop    assistant.service      # stop
sudo journalctl -u assistant.service -f       # live logs
tail -f ~/home-assistant-companion/assistant.log                # application logs
```

### Update

```bash
cd ~/home-assistant-companion
git pull
pip3 install --user --upgrade -r requirements.txt
sudo systemctl restart assistant.service
```

### Uninstall

```bash
sudo systemctl stop assistant.service
sudo systemctl disable assistant.service
sudo rm /etc/systemd/system/assistant.service
sudo systemctl daemon-reload
rm -rf ~/home-assistant-companion          # removes code, config and DB
```

---

## Manual

To run the bot by hand, without a service, without Docker.

```bash
git clone https://github.com/MrMortalMonkey/home-assistant-companion.git
cd home-assistant-companion
pip3 install --user -r requirements.txt
./install.sh               # interactive wizard → config.json
python3 assistant.py       # start the bot
```

To keep it running after disconnection:

```bash
nohup python3 assistant.py > assistant.log 2>&1 &
# or better: tmux new -s assistant, then python3 assistant.py, then Ctrl+B D to detach
```

To restart automatically at boot, use the **Native Linux** method with systemd instead.

---

## What happens on first launch?

1. **Telegram chat detection** — The bot waits for your first message to register your `chat_id`. Send anything (e.g. "hello").
2. **Security code** — A 6-digit code is sent via `sms_method` (Free Mobile / HA notification / email). Type it in Telegram to unlock the channel.
3. **Appliance questionnaire** — For each detected smart plug with power measurement, the bot asks: washing machine / dryer / dishwasher / TV / other.
4. **Household profile** — 8 quick questions: occupancy, heating, hot water, solar, goal.
5. **Electricity rate** — Automatic off-peak/peak detection or questionnaire.

Total time: **10 minutes**. After that, you don't touch anything.

---

## Verify everything works

In Telegram:

- `/help` — list of commands
- `/audit` — full report (energy, Zigbee, NAS, batteries)
- `/debug` — threads, watchdog, versions
- `/monitoring` — complete view of monitored entities

If a command returns an error, consult [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
