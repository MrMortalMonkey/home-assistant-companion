<h1 align="center">🏠 Home Assistant AI Companion</h1>
<p align="center"><strong>An autonomous, conversational AI agent for your Home Assistant</strong></p>
<p align="center">
  <a href="#-quick-install">Installation</a> ·
  <a href="docs/INSTALL.md">Documentation</a> ·
  <a href="docs/TROUBLESHOOTING.md">Troubleshooting</a> ·
  <a href="docs/BETA_CHANNEL.md">Beta channel</a>
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-v0.1.0-blue">
  <img alt="Status" src="https://img.shields.io/badge/status-beta-orange">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white">
  <img alt="Home Assistant" src="https://img.shields.io/badge/Home_Assistant-2024.1+-41BDF5?logo=home-assistant&logoColor=white">
  <img alt="AI Providers" src="https://img.shields.io/badge/AI-multi--provider-6A5ACD">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-green">
</p>

---

**Home Assistant AI Companion** is an autonomous AI agent that talks to your Home Assistant 24/7 via Telegram. It discovers your devices, understands natural language requests, tracks your energy consumption, and alerts you at the right moment.

> 💬 *"Turn on the living room light"* → your selected AI model finds the right entity, calls the right service, confirms the action.

## ✨ What it does

- **💬 Natural language control**: no more searching the UI or writing YAML. You say it, your selected AI model does it.
- **🔍 Universal detection**: Zigbee, Matter, Z-Wave, WiFi, ESPHome — everything that shows up in Home Assistant.
- **⚡ Proactive monitoring**: morning briefing, solar peak alerts, standby detection, devices offline, low batteries.
- **🧺 Appliance cycles**: automatic detection of washing machine / dryer / dishwasher cycles + real-time cost tracking.
- **🔔 Dynamic alerts**: *"Notify me if a micro-inverter goes offline"* → permanent alert created.
- **🤖 Auto-fix**: `/problem <description>` → the configured strong AI model reads the code, proposes a patch, you validate.
- **📊 Savings tracking**: every saving generated (solar peak, off-peak hours, eliminated standby) is recorded.

## 🎯 How is this different from a regular automation?

Many things listed above can be done with YAML automations or Node-RED. The difference lies in **three things YAML doesn't do**:

1. **Natural language** — you create alerts, scenes, or trigger actions in full sentences, without opening the interface or editing YAML. The AI identifies the right entities and builds the right service calls.
2. **Auto-fix** — when something breaks, you type `/problem`. The AI reads the script code, diagnoses it, proposes a patch, applies it if you approve. Zero terminal.
3. **Continuous adaptation** — the script learns your home, your rate, your devices. It adjusts its alerts based on what it observes, not on fixed rules you wrote once.

This is not a replacement for your existing automations. It's a **conversational and intelligent layer** on top of HA.

## 📋 Prerequisites

| # | What | Where to get it | Cost |
|---|------|-----------------|------|
| 1 | **Telegram Bot** | [@BotFather](https://t.me/BotFather) → `/newbot` | Free |
| 2 | **Home Assistant** with long-lived token | Profile → Long-lived access tokens | Free |
| 3 | **AI provider credentials** (Anthropic, OpenAI, OpenRouter, Ollama, LM Studio) | See provider console or local endpoint | Variable (see below) |
| 4 | **A Linux machine** (Pi, VM, NAS, HA App...) | See hardware table | Variable |

### 🔌 Reliable off-peak/peak data — recommendation (optional)

If you have a smart meter and a time-of-use rate (off-peak / peak hours), I recommend the [**ha-linky**](https://github.com/bokub/ha-linky) app (Bokub) as an official source for your off-peak/peak consumption. It uses the Enedis API via [Consumption API](https://consumption.boris.sh) (free, 3-year Enedis consent).

**Why?** Third-party energy integrations don't all support every rate. ha-linky reads directly from Enedis, so it's compatible with **all rates**.

Data delivered in D+1 (the previous morning), so it's not real-time — but it's reliable and official.

## 🚀 Quick install

Four supported methods. **Choose the one that matches your hardware**:

### 1️⃣ HA App — Easiest (HA OS / Supervised)

If you have Home Assistant OS (HA Green, Yellow, Blue, etc.):

1. In Home Assistant: **Settings → Apps → Install app → ⋮ → Repositories**
2. Add: `https://github.com/MrMortalMonkey/home-assistant-companion`
3. Find **Home Assistant AI Companion** in the list, click **Install**
4. Configure: `telegram_token`, `llm_provider`, and the matching provider key or local endpoint (HA URL and token are automatic)
5. **Start**

[📖 Detailed guide →](docs/INSTALL.md#ha-app)

### 2️⃣ Docker Compose — Most portable

On any machine with Docker (Synology NAS, Mini PC, Linux...):

```bash
git clone https://github.com/MrMortalMonkey/home-assistant-companion.git
cd home-assistant-companion
cp env.example .env     # then edit .env with your credentials
docker compose up -d
docker compose logs -f  # follow the logs
```

[📖 Detailed guide →](docs/INSTALL.md#docker)

### 3️⃣ Native Linux (Raspberry Pi, VM, server)

For full control, with systemd for auto-recovery:

```bash
git clone https://github.com/MrMortalMonkey/home-assistant-companion.git
cd home-assistant-companion
./install.sh                       # interactive CLI wizard
sudo ./scripts/install_systemd.sh  # deploy as system service
```

The `assistant.service` starts at boot, restarts on crash (`Restart=always`).

[📖 Detailed guide →](docs/INSTALL.md#native-linux)

### 4️⃣ Manual install — To understand what's happening

```bash
git clone https://github.com/MrMortalMonkey/home-assistant-companion.git
cd home-assistant-companion
pip install -r requirements.txt
./install.sh                # generates config.json
python3 assistant.py        # start the bot
```

[📖 Detailed guide →](docs/INSTALL.md#manual)

## 🎯 After installation

1. **Send a message** to your Telegram bot (anything)
   → your `chat_id` is detected automatically
2. The bot guides you: **appliance questionnaire**, **household profile**, **electricity rate**
3. Type `/help` to see all available commands

⏱ **Total time**: 10 minutes. Then the bot runs on its own.

## 🏗 What hardware?

| Machine | Min RAM | Cost | Ideal for |
|---|---|---|---|
| **HA App** (on HA OS) | 512 MB | Free | Already have HA Green/Yellow → 1 click |
| **Raspberry Pi 4/5** | 2 GB | $40-80 | Dedicated, quiet, 5 W |
| **Oracle Cloud VM** (free tier) | 1 GB | Free | Permanently free, ARM |
| **Google Cloud VM** (e2-micro) | 1 GB | Free | Free for 12 months |
| **Mini PC N100/N95** | 8 GB | $80-150 | Powerful, reliable SSD |
| **Synology NAS** (Docker) | 4 GB | Free | Already on 24/7 |

## 💰 What does it actually cost?

The script is **free and open source (MIT)**. The only recurring AI cost is whatever provider or local model runtime you choose.

| Item | Cost |
|---|---|
| Script | **Free** (MIT) |
| AI usage | Variable — depends on provider, selected model, and usage |
| Hosting | Variable — between $0 (HA App, free VM) and ~$10 (dedicated Pi) |

**On AI usage:** normal hosted-model usage (1 briefing/day, a few conversational commands, passive monitoring) generally runs around **$5–15/month**, depending on provider and model. Local providers such as Ollama or LM Studio can avoid per-token API billing. Use your provider's key-level usage limits when available; the bot's `/budget` command reports estimated token cost locally.

**On savings:** the script measures what it saves you (optimized solar, machines shifted to off-peak, eliminated standbys) and shows it in dollars via `/roi`. **You judge whether the cost/benefit ratio works for you.** Savings depend heavily on your setup: if you have solar + a heat pump + an off-peak rate, there are many levers. If you have a minimal setup, savings probably just cover the token cost — and that's fine, the value shifts to conversational convenience.

> 🧪 **Provider choice**: Anthropic, OpenAI, OpenRouter, Ollama, and LM Studio are supported. See [Configuration](docs/CONFIGURATION.md) for provider-specific options.

## 📚 Documentation

- [docs/INSTALL.md](docs/INSTALL.md) — Detailed installation guide (4 methods)
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md) — All `config.json` keys
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — Common problem resolution
- [docs/BETA_CHANNEL.md](docs/BETA_CHANNEL.md) — Beta tester mode (remote patches, **opt-in**)
- [LESSONS.md](LESSONS.md) — Known installation pitfalls and fixes

## 🛡 Security

- `config.json` contains your secrets: the installer automatically sets it to `600` permissions (owner read only)
- Telegram channel locked at startup with a 6-digit code (SMS / HA notification / email)
- All sensitive HA actions (lock, light, climate) go through a ✅/❌ confirmation button
- Beta tester mode **disabled by default** — `deploy_server` only activates if you explicitly enable it. See [BETA_CHANNEL.md](docs/BETA_CHANNEL.md) for implications.

## 📊 Project status

**Current version: v0.1.0 beta**

The code has been running in production since February 2026. It's functionally stable but has **not yet been validated on enough different installations** to be considered generally stable.

**What to expect at this stage:**
- Bugs discovered by different HA configurations
- Behaviors that need to be generalized (not all homes have solar, a heat pump, etc.)
- Refinement of AI prompts to reduce token consumption

If you're testing, feedback via [GitHub Issues](https://github.com/MrMortalMonkey/home-assistant-companion/issues) is valuable, even brief.

## 🗺 Roadmap

**Done:**
- [x] 51 Telegram commands + natural language
- [x] Universal detection (Matter / Zigbee / Z-Wave / WiFi)
- [x] Proactive monitoring engine (7am briefing, solar peak, standby, off-peak/peak, 9pm summary)
- [x] Auto-fix via `/problem`
- [x] Google Home / Alexa integration (TTS)
- [x] HA App + Docker + systemd
- [x] Documented installation, opt-in deploy_server

**Coming:**
- [x] Multi-provider AI support to reduce API cost
- [ ] Thermodynamic water heater (clamp meter)
- [ ] Web dashboard (FastAPI)
- [ ] Mobile app (React Native)
- [ ] Web dashboard documentation and screenshots

## 🤝 Support

- 🐛 **Bug** → [GitHub Issues](https://github.com/MrMortalMonkey/home-assistant-companion/issues)
- 💡 **Idea** → [GitHub Discussions](https://github.com/MrMortalMonkey/home-assistant-companion/discussions)

## 📜 License

[MIT](LICENSE) — Use, modify, share freely.

---

<p align="center">
  <strong>Home Assistant AI Companion</strong><br>
  <em>A conversational AI agent for your Home Assistant.</em><br>
  <em>It speaks your language. It learns your home. It self-corrects.</em>
</p>
