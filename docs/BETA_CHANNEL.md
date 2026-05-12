# 🧪 Beta tester mode — Remote patch channel

> ⚠️ **Opt-in mode, disabled by default.**
> If you're a regular user, **you don't need this mode**. The AI Companion works fine without it.

## What is beta mode?

Beta mode installs an additional component — `deploy_server` — that allows **MrMortalMonkey** to push fixes to your installation **remotely**, without SSH.

This is useful if:
- You're in the cirkey_name of beta testers who have accepted this channel
- You want to receive automatic fixes as soon as a bug is found and fixed in another user's installation
- You want to benefit from the "collective learning" feature (lessons discovered at one tester's home benefit everyone)

This is **unnecessary** if:
- You just want to run the AI Companion at home, without a connection to **MrMortalMonkey**
- You prefer to manually validate each update via `git pull`
- You don't want to expose an extra HTTP port (even via tunnel)

## Security implications — read before enabling

Enabling beta tester mode means:

1. **Port 8501 is open locally** — deploy_server listens on `127.0.0.1:8501`
2. **A Cloudflare Quick Tunnel is created** — it exposes port 8501 on a public HTTPS URL (e.g. `https://xxx-yyy.trycloudflare.com`)
3. **The URL is published on [ntfy.sh](https://ntfy.sh)** — on a secret topic only **MrMortalMonkey** knows
4. **All state-modifying requests are authenticated** by HMAC-SHA256 signature using your installation's unique secret (`deploy_secret` in `config.json`)

**What MrMortalMonkey CAN do with your authorization:**
- Read the source code of your installation
- Apply a Python patch (`deploy_server.py`, `skills.py`, etc.)
- Restart AI Companion services
- Check logs

**What MrMortalMonkey CANNOT do:**
- Read your `config.json` (file protected server-side)
- Execute arbitrary shell commands outside the installation directory
- Access other services on your network
- Read your `memory.db` (your personal data stays with you)

**Residual risks:**
- If `deploy_secret` leaks (physical machine theft, compromise), an attacker can push malicious patches
- If Cloudflare is compromised at the tunnel level, same risk
- These risks are real but low — which is why the mode is opt-in

## Enabling beta mode

### Prerequisites

- `cloudflared` installed on the machine:
  ```bash
  # Debian/Ubuntu/Pi
  wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
  sudo dpkg -i cloudflared-linux-amd64.deb
  # (or -arm64.deb on Pi 4/5)
  ```
- `curl`, `jq`

### Activation

From your AI Companion installation:

```bash
cd <assistant-folder>
./scripts/enable_beta_channel.sh
```

The script will:
1. Install two systemd services: `deploy_server.service` and `cloudflared_tunnel.service`
2. Install a watchdog (`infra_watchdog.timer`) that checks health every 2 min
3. Publish the current tunnel URL on ntfy.sh (topic derived from your `deploy_secret`)
4. Display the topic to send to **MrMortalMonkey** so they can connect

**You must then send the topic to MrMortalMonkey** through a private channel so patches can be pushed to you. Without this topic, no one can connect.

### Disabling

```bash
./scripts/disable_beta_channel.sh
```

Removes the three systemd services and kills the tunnels. Your installation becomes a standard installation again.

## What happens concretely

Once enabled:

- `deploy_server.service` and `cloudflared_tunnel.service` run continuously
- Every hour, the tunnel URL is republished on ntfy.sh (24h window)
- The watchdog checks every 2 min that services respond, and restarts them automatically if KO
- At machine reboot, everything restarts automatically

## Logging

All actions (reads, patches, restarts) are logged server-side in:
- `deploy.log` — deploy_server actions
- `watchdog.log` — periodic health checks
- `handoff.log` — migrations / bootstrap

You can consult them at any time.

## FAQ

**Q: Can MrMortalMonkey see my secrets (`telegram_token`, `ha_token`, `anthropic_api_key`)?**
A: No. `config.json` is explicitly protected (`FORBIDDEN_PATHS = {"config.json"}` in the deploy_server code). Only Python code files are readable.

**Q: What if I refuse a patch?**
A: Patches don't apply automatically — MrMortalMonkey pushes them and they apply, but you can do a `/rollback` at any time to return to the previous version. All patches make an automatic backup.

**Q: Can I activate this temporarily?**
A: Yes. Enable to receive a fix, then disable with `disable_beta_channel.sh`.

**Q: Is this necessary to use the AI Companion?**
A: **No, absolutely not.** The AI Companion works autonomously without this mode. You'll just receive updates via classic `git pull` instead of automatically.
