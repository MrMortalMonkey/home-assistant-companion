#!/usr/bin/env python3
import shutil as _shutil, os as _os
_cache = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "__pycache__")
if _os.path.exists(_cache): _shutil.rmtree(_cache, ignore_errors=True)

from skills import *
from skills import (
    _alert_night_ghost_consumption,
    _alert_freezer_outage,
    _alert_zigbee_device_mort,
    _backup_auto_db,
    _check_voice_scripts,
    _collect_appliance_candidates,
    _conversational_onboarding_message,
    _cycle_intelligence,
    _detect_water_leak,
    _detect_vacation_mode,
    _detect_internet_outage,
    _check_dynamic_watches,
    _heartbeat_observe,
    _monitoring_deploy_server,
    _notify_tempo_ejp,
    _rollback_on_repeated_errors,
)
from shared import (_wizard_step, _wizard_save_config, _is_authorized_chat, transcribe_voice,
    _state_plugs, _grace_ended_at, _powers_history, _last_high_phase,
    _laundry_reminder_sent, _anti_crease_detected, _watchdog, _entities_already_detected,
    _plugs_snapshot, _power_outage_alertd, _snapshot_valid)
import shared

import json, os, re, requests, sqlite3, smtplib, time, threading
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
import llm_provider


def backup_sqlite():
    while True:
        now = datetime.now()
        if now.hour == 2 and now.minute == 0:
            ok = send_email(
                f"[AI Companion] Backup memory.db — {now.strftime('%d/%m/%Y')}",
                f"Automatic backup. Date: {now.isoformat()}",
                attachment=DB_PATH
            )
            if not ok:
                telegram_send("⚠️ BACKUP SQLite — EMAIL FAILED")
            time.sleep(61)
        time.sleep(30)


def keepalive():
    path = os.path.join(BASE_DIR, "keepalive.log")
    while True:
        try:
            with open(path, "a") as f:
                f.write(f"{datetime.now().isoformat()} keepalive\n")
            with open(path, "r") as f:
                lines = f.readlines()
            if len(lines) > 500:
                with open(path, "w") as f:
                    f.writelines(lines[-200:])
        except Exception:
            pass
        time.sleep(3600)


def _bind_first_chat(chat_id):
    """Bind the first Telegram chat that talks to a fresh HA App install."""
    if chat_id and not str(CFG.get("telegram_chat_id", "")).strip():
        CFG["telegram_chat_id"] = str(chat_id)
        _wizard_save_config()
        log.info(f"Telegram chat_id bound: {chat_id}")
        telegram_send(
            "✅ Telegram connected.\n\n"
            + _conversational_onboarding_message(),
            force=True
        )
        mem_set("conversational_onboarding_sent", "yes")
        return True
    return False


def _wizard_handle_message(text):
    """Handles a message during the wizard. Returns True if consumed."""
    global CFG
    step = _wizard_step()
    if not step:
        return False

    text = text.strip()

    # ═══ STEP 1: Home Assistant URL ═══
    if step == "ha_url":
        url = text.rstrip("/")
        if not url.startswith("http"):
            url = "http://" + url
        if ":" not in url.split("//", 1)[-1] and "duckdns" not in url and ".local" not in url:
            url += ":8123"

        # Connection test
        try:
            r = requests.get(f"{url}/api/", timeout=10, verify=False)
            if r.status_code == 401:
                # HA reachable but token required = OK
                CFG["ha_url"] = url
                CFG["_wizard_step"] = "ha_token"
                _wizard_save_config()
                telegram_send(
                    f"✅ Home Assistant found: {url}\n\n"
                    f"📡 STEP 2/3 — Access token\n"
                    f"Create a long-lived token in HA:\n"
                    f"  Profile → Long-lived access tokens → Create token\n\n"
                    f"Send me the token (starts with eyJ...)"
                )
                return True
            elif r.status_code == 200:
                data = r.json()
                if data.get("message") == "API running.":
                    # API open without auth (rare)
                    CFG["ha_url"] = url
                    CFG["_wizard_step"] = "ha_token"
                    _wizard_save_config()
                    telegram_send(
                        f"✅ Home Assistant accessible: {url}\n\n"
                        f"📡 STEP 2/3 — Access token\n"
                        f"Send me your HA long-lived token (eyJ...)"
                    )
                    return True
        except requests.exceptions.SSLError:
            # HTTPS with self-signed certificate
            CFG["ha_url"] = url
            CFG["_wizard_step"] = "ha_token"
            _wizard_save_config()
            telegram_send(
                f"✅ Home Assistant found: {url} (self-signed SSL)\n\n"
                f"📡 STEP 2/3 — Access token\n"
                f"Send me your HA long-lived token (eyJ...)"
            )
            return True
        except Exception as e:
            telegram_send(
                f"❌ Unable to reach {url}\n"
                f"Error: {str(e)[:100]}\n\n"
                f"Check the URL and try again."
            )
            return True

        telegram_send(
            f"❌ Home Assistant not found at {url}\n"
            f"Make sure HA is running and the URL is correct."
        )
        return True

    # ═══ STEP 2: HA Token ═══
    if step == "ha_token":
        token = text.strip()
        if not token.startswith("eyJ"):
            telegram_send(
                "❌ This is not a valid HA token.\n"
                "The token starts with eyJ... (JWT format).\n"
                "Create it in HA: Profile → Long-lived access tokens"
            )
            return True

        # Token test
        try:
            headers = {"Authorization": f"Bearer {token}"}
            r = requests.get(f"{CFG['ha_url']}/api/", headers=headers, verify=False, timeout=10)
            if r.status_code == 200 and r.json().get("message") == "API running.":
                CFG["ha_token"] = token
                # Count entities
                r2 = requests.get(f"{CFG['ha_url']}/api/states", headers=headers, verify=False, timeout=15)
                nb_entities = len(r2.json()) if r2.status_code == 200 else "?"
                CFG["_wizard_step"] = "provider_key"
                _wizard_save_config()
                provider_name = CFG.get("llm_provider", "anthropic")
                provider = llm_provider.PROVIDERS.get(provider_name, llm_provider.PROVIDERS["anthropic"])
                provider_label = provider.get("name", provider_name)
                telegram_send(
                    f"✅ Home Assistant connected! {nb_entities} entities detected.\n\n"
                    f"🧠 STEP 3/4 — AI provider credentials\n"
                    f"Selected provider: {provider_label}\n\n"
                    f"Send me the API key for this provider."
                )
                return True
            else:
                telegram_send(
                    f"❌ Token rejected by Home Assistant (HTTP {r.status_code}).\n"
                    f"Check the token and try again."
                )
                return True
        except Exception as e:
            telegram_send(f"❌ HA connection error: {str(e)[:100]}")
            return True

    # ═══ STEP 3: LLM API Key ═══
    if step == "provider_key":
        key = text.strip()
        provider_name = CFG.get("llm_provider", "anthropic")
        provider = llm_provider.PROVIDERS.get(provider_name, llm_provider.PROVIDERS["anthropic"])

        # Validate the key based on provider
        if provider_name == "anthropic":
            if not key.startswith("sk-ant-"):
                telegram_send(
                    "❌ This is not a valid Anthropic API key.\n"
                    "The key starts with sk-ant-...\n"
                    "Create it at console.anthropic.com → API Keys"
                )
                return True
        elif provider_name == "openai":
            if not key.startswith("sk-"):
                telegram_send(
                    "❌ This is not a valid OpenAI API key.\n"
                    "The key starts with sk-...\n"
                    "Create it at platform.openai.com/api-keys"
                )
                return True
        elif provider_name in ("openrouter",):
            if not key or len(key) < 10:
                telegram_send(
                    "❌ Invalid API key.\n"
                    "Create one at openrouter.ai/keys"
                )
                return True

        # Key test
        try:
            if provider_name == "anthropic":
                import anthropic
                client = anthropic.Anthropic(api_key=key)
                r = client.messages.create(
                    model=provider.get("default_model", "claude-haiku-4-5-20251001"), max_tokens=10,
                    messages=[{"role": "user", "content": "Say hello"}]
                )
            else:
                blocks, t_in, t_out = llm_provider.llm_completion(
                    {**CFG, "llm_provider": provider_name,
                     "openai_api_key": key if provider_name == "openai" else "",
                     "openrouter_api_key": key if provider_name == "openrouter" else "",
                     "anthropic_api_key": key if provider_name == "anthropic" else ""},
                    [{"role": "user", "content": "Say hello"}],
                    model=provider.get("default_model", "gpt-4o-mini"),
                    max_tokens=10
                )
                if blocks is None:
                    raise Exception("API did not respond")

            if provider_name == "anthropic":
                CFG["anthropic_api_key"] = key
            elif provider_name == "openai":
                CFG["openai_api_key"] = key
            elif provider_name == "openrouter":
                CFG["openrouter_api_key"] = key

            CFG["_wizard_step"] = "sms_method"
            _wizard_save_config()
            provider_label = provider.get("name", provider_name)
            telegram_send(
                f"✅ {provider_label} connected!\n\n"
                "🔐 STEP 4/4 — Security (unlock code)\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "The assistant locks the channel at startup.\n"
                "A security code will be sent to you to unlock it.\n\n"
                "How would you like to receive the code?"
            )
            telegram_send_buttons(
                "Choose the code delivery method:",
                [
                    {"text": "📱 SMS Free Mobile", "callback_data": "wizard_sms:free_mobile"},
                    {"text": "🔔 HA Notification", "callback_data": "wizard_sms:ha_notify"},
                    {"text": "📧 Email", "callback_data": "wizard_sms:email"},
                ]
            )
            return True
        except Exception as e:
            telegram_send(
                f"❌ API key rejected by the selected AI provider.\n"
                f"Error: {str(e)[:100]}\n\n"
                f"Check the key and try again."
            )
            return True

    # ═══ STEP 4a: Free Mobile credentials ═══
    if step == "sms_free_user":
        CFG["free_mobile_user"] = text.strip()
        CFG["_wizard_step"] = "sms_free_pass"
        _wizard_save_config()
        telegram_send("Free Mobile API password:\n(Subscriber area → My options → SMS notifications)")
        return True

    if step == "sms_free_pass":
        CFG["free_mobile_pass"] = text.strip()
        # Send test
        CFG["sms_method"] = "free_mobile"
        _wizard_save_config()
        code_test = "TEST"
        try:
            url = f"https://smsapi.free-mobile.fr/sendmsg?user={CFG['free_mobile_user']}&pass={CFG['free_mobile_pass']}&msg=AI_Companion:code_test_{code_test}"
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                _wizard_complete()
                return True
            else:
                telegram_send(f"❌ Free Mobile rejected the request (HTTP {r.status_code}).\nCheck your username and API password.")
                CFG["_wizard_step"] = "sms_free_user"
                _wizard_save_config()
                telegram_send("Please resend your Free Mobile username (8 digits):")
                return True
        except Exception as e:
            telegram_send(f"❌ Free Mobile error: {str(e)[:100]}")
            CFG["_wizard_step"] = "sms_free_user"
            _wizard_save_config()
            return True

    # ═══ STEP 4b: HA Notify service ═══
    if step == "sms_ha_notify_service":
        service_name = text.strip().replace("notify.", "").replace("mobile_app_", "")
        # If the user typed the full name, extract the useful part
        if text.strip().startswith("mobile_app_"):
            service_name = text.strip()
        elif not text.strip().startswith("notify."):
            service_name = f"mobile_app_{service_name}"
        CFG["ha_notify_service"] = service_name
        CFG["sms_method"] = "ha_notify"
        _wizard_save_config()
        # Test
        try:
            url_test = f"{CFG['ha_url']}/api/services/notify/{service_name}"
            headers = {"Authorization": f"Bearer {CFG['ha_token']}", "Content-Type": "application/json"}
            payload_test = {"title": "🔐 AI Companion Test", "message": "If you see this notification, it's configured!", "data": {"priority": "high"}}
            r = requests.post(url_test, json=payload_test, headers=headers, verify=False, timeout=10)
            if r.status_code == 200:
                telegram_send("✅ Test notification sent! Check your phone.")
                _wizard_complete()
                return True
            else:
                # List available notify services
                try:
                    r2 = requests.get(f"{CFG['ha_url']}/api/services", headers=headers, verify=False, timeout=10)
                    services = [s["services"] for s in r2.json() if s.get("domain") == "notify"]
                    notify_list = []
                    if services:
                        notify_list = list(services[0].keys())
                except Exception:
                    notify_list = []
                msg = f"❌ Service notify/{service_name} not found.\n"
                if notify_list:
                    msg += "\nAvailable services:\n" + "\n".join(f"  • {n}" for n in notify_list[:10])
                msg += "\n\nResend the service name:"
                telegram_send(msg)
                return True
        except Exception as e:
            telegram_send(f"❌ Error: {str(e)[:100]}\nTry again:")
            return True

    # ═══ STEP 4c: Email ═══
    if step == "sms_email_addr":
        email = text.strip()
        if "@" not in email:
            telegram_send("❌ Invalid email address. Try again:")
            return True
        CFG["email_dest"] = email
        CFG["smtp_user"] = email  # Default = same email
        CFG["_wizard_step"] = "sms_email_smtp"
        _wizard_save_config()
        telegram_send(
            f"SMTP server for {email}:\n"
            "Common examples:\n"
            "  • Gmail: smtp.gmail.com\n"
            "  • Outlook: smtp.office365.com\n"
            "  • Yahoo: smtp.mail.yahoo.com"
        )
        return True

    if step == "sms_email_smtp":
        CFG["smtp_host"] = text.strip()
        CFG["smtp_port"] = 587
        CFG["_wizard_step"] = "sms_email_pass"
        _wizard_save_config()
        telegram_send(
            "SMTP password (or app password):\n"
            "For Gmail: create an app password\n"
            "(Google Account → Security → App passwords)"
        )
        return True

    if step == "sms_email_pass":
        CFG["smtp_pass"] = text.strip()
        CFG["sms_method"] = "email"
        _wizard_save_config()
        # Test
        try:
            msg_test = MIMEText("If you receive this email, the configuration is correct.")
            msg_test["Subject"] = "🔐 AI Companion Test"
            msg_test["From"] = CFG.get("smtp_user", "")
            msg_test["To"] = CFG["email_dest"]
            with smtplib.SMTP(CFG["smtp_host"], CFG.get("smtp_port", 587)) as s:
                s.starttls()
                s.login(CFG["smtp_user"], CFG["smtp_pass"])
                s.send_message(msg_test)
            telegram_send("✅ Test email sent! Check your inbox.")
            _wizard_complete()
            return True
        except Exception as e:
            telegram_send(f"❌ SMTP error: {str(e)[:100]}\nCheck the settings.")
            CFG["_wizard_step"] = "sms_email_addr"
            _wizard_save_config()
            telegram_send("Please resend your email address:")
            return True

    return False


def _wizard_complete():
    """Completes the wizard and starts the system."""
    CFG.pop("_wizard_step", None)
    _wizard_save_config()
    method_names = {"free_mobile": "SMS Free Mobile", "ha_notify": "HA Notification", "email": "Email"}
    method_name = method_names.get(CFG.get("sms_method", ""), "?")
    telegram_send(
        "🎉 SETUP COMPLETE\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"• Home Assistant: {CFG['ha_url']}\n"
        f"• Telegram: connected\n"
        f"• AI provider: connected\n"
        f"• Security: {method_name}\n\n"
        "🚀 First scan in progress...\n"
        "The assistant will scan your entities, discover your devices,\n"
        "and set up monitoring automatically.\n\n"
        "Type /help to see all commands."
    )
    log.info(f"🎉 Wizard complete — SMS method: {CFG.get('sms_method')}")


def validation_started():
    results = []
    try:
        r = requests.get(f"https://api.telegram.org/bot{CFG['telegram_token']}/getMe", timeout=10)
        results.append("✅ Telegram OK" if r.status_code == 200 else f"❌ Telegram {r.status_code}")
    except Exception as e:
        results.append(f"❌ Telegram: {e}")

    try:
        r = ha_get("")
        results.append("✅ Home Assistant OK" if r and r.get("message") == "API running."
                         else "❌ Home Assistant unreachable")
    except Exception as e:
        results.append(f"❌ HA: {e}")

    try:
        provider_name = CFG.get("llm_provider", "anthropic")
        if provider_name == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=CFG["anthropic_api_key"])
            client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=5,
                                   messages=[{"role": "user", "content": "ok"}])
        else:
            blocks, _, _ = llm_provider.llm_completion(
                CFG, [{"role": "user", "content": "ok"}], max_tokens=5
            )
            if blocks is None:
                raise Exception("API did not respond")
        provider_label = llm_provider.PROVIDERS.get(provider_name, {}).get("name", provider_name)
        results.append(f"✅ {provider_label} OK")
    except Exception as e:
        results.append(f"❌ LLM: {e}")

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("SELECT 1")
        conn.close()
        results.append("✅ SQLite OK")
    except Exception as e:
        results.append(f"❌ SQLite: {e}")

    summary  = f"🚀 Home Assistant AI Companion {VERSION}\n━━━━━━━━━━━━━━━━━━━━\n"
    summary += "\n".join(results)
    summary += f"\n━━━━━━━━━━━━━━━━━━━━\nMode: {MODE}"
    log.info(summary)
    return summary


def is_night_hour():
    """Night = sun below the horizon"""
    try:
        states = ha_get("states")
        if states:
            sun = next((e for e in states if e["entity_id"] == "sun.sun"), None)
            if sun:
                return sun["state"] == "below_horizon"
    except Exception:
        pass
    h = datetime.now().hour
    return h >= 22 or h < 7


def check_night_urgencies():
    alerts = []
    try:
        r = ha_get("")
        if not r or r.get("message") != "API running.":
            alerts.append("🚨 ALERT — HA UNREACHABLE")
    except Exception:
        alerts.append("🚨 ALERT — HA UNREACHABLE")

    states = ha_get("states")
    if states:
        index = {e["entity_id"]: e for e in states}
        for entity_id, sous_cat, room in entity_map_get_by_category("nas"):
            if "volume" in sous_cat.lower() and entity_id in index:
                v = index[entity_id]
                if v["state"] not in ["normal", "on", "unknown", "unavailable"]:
                    alerts.append(f"🚨 NAS VOLUME DEGRADED: {entity_id} = {v['state']}")
    return alerts


def watchdog_interne():
    """Watchdog thread — monitors internal anomalies"""
    time.sleep(300)
    while True:
        try:
            now = datetime.now()
            anomalies = []

            last_mon = _watchdog.get("monitoring_last_run")
            if last_mon and (now - last_mon).total_seconds() > 900:
                anomalies.append(
                    f"⚠️ Monitoring thread silent for "
                    f"{int((now-last_mon).total_seconds()//60)} min"
                )

            last_pri = _watchdog.get("plugs_last_run")
            if last_pri and (now - last_pri).total_seconds() > 600:
                anomalies.append(
                    f"⚠️ Outlets thread silent for "
                    f"{int((now-last_pri).total_seconds()//60)} min"
                )

            blocked = _watchdog.get("offset_blocked_since")
            if blocked and (now - blocked).total_seconds() > 300:
                anomalies.append(
                    f"🚨 Telegram offset stuck for "
                    f"{int((now-blocked).total_seconds()//60)} min"
                )

            errors = _watchdog.get("errors", [])
            if len(errors) >= 3:
                anomalies.append(
                    f"🚨 {len(errors)} exceptions in 1h\n"
                    f"Last: {errors[-1][1][:100]}"
                )

            if anomalies:
                log.warning(f"Watchdog: {'; '.join(a[:60] for a in anomalies)}")

        except Exception as ex:
            log.error(f"❌ watchdog_interne: {ex}")

        time.sleep(300)


def _scan_infiltration_auto():
    """Silent infiltration scan every 1h — AI categorization of unknown entities."""
    while True:
        time.sleep(1 * 3600)
        try:
            states = ha_get("states")
            if states:
                index = {e["entity_id"]: e for e in states}
                handle_pending_entities(index)
                log.info("🔍 Automatic infiltration scan complete")
        except Exception as ex:
            log.error(f"❌ scan_infiltration_auto: {ex}")


def audit_auto():
    global last_audit
    while True:
        now = time.time()
        interval = CFG.get("audit_interval_sec", 1800)
        if now - last_audit >= interval:
            log.info("⏱️ Silent audit...")

            # (removed: skip audit if channel locked)

            if is_night_hour():
                alerts = check_night_urgencies()
                for a in alerts:
                    telegram_send(a)
                    log.warning(a)
            else:
                states = ha_get("states")
                if states:
                    ko = [e for e in states if e["state"] in ["unavailable", "unknown"]]
                    mem_set("last_audit_ko", len(ko))
                    mem_set("last_audit_hour", datetime.now().strftime("%d/%m/%Y %H:%M"))
                    log.info(f"Silent audit: {len(ko)} entities offline")

            last_audit = now

            summary_days = CFG.get("summary_days", 4)
            last_summary = mem_get("last_summary")
            started = mem_get("ha_scan_date")
            if started and not last_summary:
                try:
                    started_at = datetime.fromisoformat(started)
                    if (datetime.now() - started_at).total_seconds() >= summary_days * 86400:
                        threading.Thread(target=automatic_summary, daemon=True).start()
                except Exception:
                    pass

        time.sleep(60)


def monitoring_batteries():
    while True:
        now = datetime.now()
        if now.hour == 9 and now.minute < 5:
            log.info("🔋 Battery check at 9am...")
            states = ha_get("states")
            if states:
                alerts = []
                for e in states:
                    eid = e["entity_id"]
                    attrs_b = e.get("attributes", {})
                    is_battery = (
                        attrs_b.get("device_class") == "battery" or
                        "state_of_charge" in eid.lower() or
                        ("battery" in eid.lower() and "power" not in eid.lower())
                    )
                    unit_b = attrs_b.get("unit_of_measurement", "")
                    if unit_b and unit_b not in ["%", ""]:
                        is_battery = False
                    if not is_battery:
                        continue
                    try:
                        val = float(e["state"])
                    except Exception:
                        continue

                    carto = entity_map_get(eid)
                    room = carto[2] if carto else ""
                    battery_set(eid, room, int(val))

                    if val < 20:
                        last = battery_get_last_alert(eid)
                        if last:
                            try:
                                delta = (now - datetime.fromisoformat(last)).total_seconds()
                                if delta < 86400:
                                    continue
                            except Exception:
                                pass
                        room_str = f" [{room}]" if room else ""
                        icon = "🚨" if val < 10 else "⚠️"
                        alerts.append(f"{icon} {eid}{room_str} : {int(val)}%")
                        battery_set_alert(eid)

                if alerts:
                    telegram_send("🔋 LOW BATTERIES:\n" + "\n".join(alerts))
                else:
                    log.info("✅ Batteries: all OK")

            time.sleep(3600)
        time.sleep(60)


def _state_index(states):
    return {e.get("entity_id"): e for e in states or [] if e.get("entity_id")}


def _watts_from_state(entity):
    if not entity or entity.get("state") in ("unavailable", "unknown", ""):
        return None
    try:
        return float(str(entity.get("state")).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _monitored_appliances():
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT entity_id, appliance_type, custom_name FROM appliances WHERE monitored=1"
        ).fetchall()
        conn.close()
        return rows
    except Exception as e:
        log.debug(f"monitored_appliances: {e}")
        return []


def _cycle_grace_minutes(appliance_type, entity_id):
    if appliance_type == "dishwasher":
        return GRACE_AFTER_DISHWASHER
    if appliance_type == "dryer":
        return GRACE_AFTER_DRYING
    phase = _last_high_phase.get(entity_id)
    if phase == "spin":
        return GRACE_AFTER_SPIN
    return GRACE_AFTER_WASH


def _record_cycle_sample(entity_id, watts):
    ts = datetime.now().isoformat()
    _powers_history.setdefault(entity_id, []).append((ts, watts))
    _powers_history[entity_id] = _powers_history[entity_id][-1500:]
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO cycle_measurements (entity_id, watts, ts) VALUES (?, ?, ?)",
            (entity_id, watts, ts)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.debug(f"record_cycle_sample: {e}")


def _estimate_cycle_kwh(samples):
    total_wh = 0.0
    parsed = []
    for ts, watts in samples:
        try:
            parsed.append((datetime.fromisoformat(ts), float(watts)))
        except Exception:
            continue
    parsed.sort(key=lambda item: item[0])
    for (t1, w1), (t2, w2) in zip(parsed, parsed[1:]):
        hours = max(0, min((t2 - t1).total_seconds(), 300)) / 3600
        total_wh += ((w1 + w2) / 2) * hours
    return round(total_wh / 1000, 3)


def monitoring_core():
    """Main autonomous monitoring loop."""
    while True:
        now = datetime.now()
        try:
            states = ha_get("states")
            _watchdog["monitoring_last_run"] = now
            if states:
                index = _state_index(states)
                try:
                    _cycle_intelligence(states, index, now)
                except Exception as e:
                    log.error(f"monitoring_core intelligence: {e}")

                for fn in (
                    _alert_night_ghost_consumption,
                    _alert_freezer_outage,
                    _detect_water_leak,
                    _alert_zigbee_device_mort,
                    _heartbeat_observe,
                    _check_voice_scripts,
                    _check_dynamic_watches,
                ):
                    try:
                        fn(index, now)
                    except Exception as e:
                        log.debug(f"{fn.__name__}: {e}")

                background_fns = [
                    _detect_vacation_mode,
                    _backup_auto_db,
                    _detect_internet_outage,
                    _rollback_on_repeated_errors,
                    _monitoring_deploy_server,
                ]
                if CFG.get("enable_tempo_ejp", False):
                    background_fns.append(_notify_tempo_ejp)
                for fn in background_fns:
                    try:
                        fn(now)
                    except Exception as e:
                        log.debug(f"{fn.__name__}: {e}")
            else:
                try:
                    _detect_internet_outage(now)
                except Exception:
                    pass
        except Exception as e:
            log.error(f"monitoring_core: {e}")
            _watchdog["errors"].append((datetime.now(), f"monitoring_core: {e}"))
        time.sleep(300)


def monitoring_plugs():
    """Watch configured smart outlets and close appliance cycles automatically."""
    while True:
        if not CFG.get("enable_appliance_detection", True):
            time.sleep(300)
            continue
        sleep_for = PLUG_POLL_IDLE
        try:
            states = ha_get("states")
            _watchdog["plugs_last_run"] = datetime.now()
            if states:
                index = _state_index(states)
                rows = _monitored_appliances()
                has_active_cycle = any(state == "active" for state in _state_plugs.values())
                sleep_for = PLUG_POLL_ACTIVE if has_active_cycle else PLUG_POLL_IDLE

                for entity_id, appliance_type, custom_name in rows:
                    entity = index.get(entity_id)
                    watts = _watts_from_state(entity)
                    if watts is None:
                        continue

                    friendly_name = custom_name or entity.get("attributes", {}).get("friendly_name", entity_id)
                    state = _state_plugs.get(entity_id)

                    if watts > 500:
                        _last_high_phase[entity_id] = "spin"
                    elif watts > CYCLE_END_W:
                        _last_high_phase.setdefault(entity_id, "wash")

                    if state != "active" and watts >= CYCLE_START_W:
                        solar_w = 0
                        try:
                            solar_w = ha_get_current_solar_production(states)
                        except Exception:
                            pass
                        cycle_started_at(entity_id, friendly_name, solar_w)
                        _state_plugs[entity_id] = "active"
                        _grace_ended_at.pop(entity_id, None)
                        _powers_history[entity_id] = []
                        _laundry_reminder_sent.pop(entity_id, None)
                        log.info(f"🔄 Cycle started: {friendly_name} ({int(watts)}W)")
                        telegram_send(f"🔄 Cycle started: {friendly_name}")

                    if _state_plugs.get(entity_id) != "active":
                        continue

                    _record_cycle_sample(entity_id, watts)

                    if watts > CYCLE_END_W:
                        _grace_ended_at.pop(entity_id, None)
                        continue

                    now = datetime.now()
                    if entity_id not in _grace_ended_at:
                        grace_min = _cycle_grace_minutes(appliance_type, entity_id)
                        _grace_ended_at[entity_id] = now + timedelta(minutes=grace_min)
                        log.info(f"⏳ Cycle grace started: {friendly_name} ({grace_min} min)")
                        continue

                    if now < _grace_ended_at[entity_id]:
                        continue

                    samples = _powers_history.get(entity_id, [])
                    consumption_kwh = _estimate_cycle_kwh(samples)
                    result = cycle_ended_at(entity_id, consumption_kwh)
                    _state_plugs.pop(entity_id, None)
                    _grace_ended_at.pop(entity_id, None)
                    _last_high_phase.pop(entity_id, None)
                    _laundry_reminder_sent.pop(entity_id, None)
                    if result:
                        telegram_send(
                            f"✅ Cycle finished: {friendly_name}\n"
                            f"Duration: {result['duration_min']} min\n"
                            f"Energy: {result['consumption_kwh']:.2f} kWh\n"
                            f"Cost: {result['cost_grid']:.2f}"
                        )
                    log.info(f"✅ Cycle ended: {friendly_name} ({consumption_kwh:.2f} kWh)")
        except Exception as e:
            log.error(f"monitoring_plugs: {e}")
            _watchdog["errors"].append((datetime.now(), f"monitoring_plugs: {e}"))
        time.sleep(sleep_for)


def main():
    global channel_locked
    log.info(f"=== Home Assistant AI Companion {VERSION} starting ===")

    init_db()

    # ═══ WIZARD MODE: incomplete config → pure Telegram polling ═══
    if _wizard_step():
        log.info("🧙 Wizard mode — waiting for configuration via Telegram")
        # Minimal polling loop (no HA, no monitoring)
        offset = None
        try:
            r_off = requests.get(
                f"https://api.telegram.org/bot{CFG['telegram_token']}/getUpdates",
                params={"timeout": 1, "offset": -1}, timeout=10
            )
            if r_off.status_code == 200:
                results = r_off.json().get("result", [])
                if results:
                    offset = results[-1]["update_id"] + 1
        except Exception:
            pass

        while _wizard_step():
            try:
                updates = telegram_get_updates(offset)
                for update in updates:
                    offset = update["update_id"] + 1
                    # Callbacks (buttons)
                    if "callback_query" in update:
                        cq = update["callback_query"]
                        chat_cq = str(cq.get("message", {}).get("chat", {}).get("id", ""))
                        if _is_authorized_chat(chat_cq):
                            handle_callback(cq)
                        continue
                    # Text messages
                    if "message" not in update:
                        continue
                    msg = update["message"]
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", "").strip()
                    _bind_first_chat(chat_id)
                    if not text or not _is_authorized_chat(chat_id):
                        continue
                    _wizard_handle_message(text)
                time.sleep(2)
            except Exception as e:
                log.error(f"Wizard polling: {e}")
                time.sleep(5)

        # Wizard complete → reload config and continue normally
        log.info("🎉 Wizard complete — normal startup")
        CFG.update(load_config())

    # Force timestamp if missing (migration v1.5.0)
    if not mem_get("channel_unlocked_at"):
        mem_set("channel_unlocked_at", datetime.now().isoformat())

    # Initialize last_unlock on first launch
    if not mem_get("last_unlock"):
        mem_set("last_unlock", datetime.now().isoformat())

    summary = validation_started()

    # ═══ CHANNEL SECURITY ═══
    # Last code < 24h → auto-unlocked. Otherwise → SMS.
    last_code = mem_get("last_unlock")
    skip_sms = False
    if MODE == "DEV":
        skip_sms = True
        log.info("🔓 DEV mode — channel open without SMS")
    if last_code:
        try:
            dt = datetime.strptime(last_code[:19], "%Y-%m-%dT%H:%M:%S")
            if (datetime.now() - dt).total_seconds() < 86400:
                skip_sms = True
                shared.channel_locked = False
        except Exception:
            pass

    if skip_sms:
        telegram_send(_conversational_onboarding_message(), force=True)
    else:
        shared.channel_locked = True
        send_code_sms()
        telegram_send("Home Assistant AI Companion is online.\nEnter the SMS code to unlock.", force=True)

    current_states = ha_get("states")
    if current_states:
        compare_entities_on_startup(current_states)

    conn = sqlite3.connect(DB_PATH)
    nb_carto = conn.execute('SELECT COUNT(*) FROM entity_map').fetchone()[0]
    conn.close()

    # Role discovery at startup
    if current_states:
        try:
            discover_roles(current_states)
        except Exception:
            pass

    if not mem_get("ha_scan_date") or nb_carto == 0:
        scan_ha_complete()
    else:
        threading.Thread(target=discover_automatically, args=(current_states,), daemon=True).start()

    # Background threads
    threading.Thread(target=backup_sqlite,         daemon=True).start()
    threading.Thread(target=keepalive,              daemon=True).start()
    threading.Thread(target=audit_auto,             daemon=True).start()
    threading.Thread(target=monitoring_batteries, daemon=True).start()
    threading.Thread(target=monitoring_core, daemon=True).start()
    threading.Thread(target=monitoring_plugs,    daemon=True).start()
    threading.Thread(target=watchdog_interne,       daemon=True).start()
    threading.Thread(target=_scan_infiltration_auto, daemon=True).start()

    # Restore the state of ongoing cycles from SQLite
    try:
        conn_cycles = sqlite3.connect(DB_PATH)
        open_cycles = conn_cycles.execute(
            "SELECT entity_id, started_at FROM appliance_cycles WHERE ended_at IS NULL"
        ).fetchall()
        for eid, started_at in open_cycles:
            # Load measurements from SQLite
            rows = conn_cycles.execute(
                "SELECT ts, watts FROM cycle_measurements WHERE entity_id=? ORDER BY ts", (eid,)
            ).fetchall()
            _powers_history[eid] = [(ts, w) for ts, w in rows]
            nb = len(rows)

            app = appliance_get(eid)
            app_name = app["name"] if app and app.get("name") else eid
            duration_min = 0
            try:
                duration_min = (datetime.now() - datetime.fromisoformat(started_at)).total_seconds() / 60
            except Exception:
                pass

            # Read current power
            power_now = 0
            if current_states:
                e_now = {e["entity_id"]: e for e in current_states}.get(eid)
                if e_now and e_now["state"] not in ("unavailable", "unknown"):
                    try:
                        power_now = float(e_now["state"])
                    except Exception:
                        pass

            if power_now > 10:
                # Machine still running → restore silently
                _state_plugs[eid] = "active"
                _laundry_reminder_sent[eid] = True
                log.info(f"🔄 Cycle restored: {app_name} ({int(duration_min)} min, {int(power_now)}W)")
            else:
                # Machine stopped → ask the user (no double notification)
                _state_plugs[eid] = "pending_restart"
                telegram_send_buttons(
                    f"🔄 Restart — {app_name} had an ongoing cycle ({int(duration_min)} min).\n"
                    f"Current power: {int(power_now)}W\n\n"
                    f"Is the cycle finished?",
                    [
                        {"text": "✅ Yes, done", "callback_data": f"cycle_ended_at:{eid}"},
                        {"text": "🔄 No, still running", "callback_data": f"cycle_continue:{eid}"},
                    ]
                )
                log.info(f"🔄 Cycle pending: {app_name} ({int(duration_min)} min, {int(power_now)}W) → question")
        conn_cycles.close()
    except Exception:
        pass

    log.info(f"✅ 8 threads started")
    mem_set("send_md_now", "yes")

    # First startup: announce readiness and let normal chat drive setup.
    current_rate, nb_rate = skill_get("pricing")
    if not current_rate or "type" not in current_rate:
        log.info("⚡ Rate not configured — waiting for normal chat")

    # Device identification from smart outlets, HA Energy, and power sensors
    try:
        candidates = _collect_appliance_candidates()
        if candidates:
            log.info(f"🔌 {len(candidates)} power consumer candidate(s) found — waiting for user instruction")
        else:
            log.info("🔌 All detected power consumers are identified")
    except Exception as ex_app:
        log.error(f"🔌 Device diagnostic error: {ex_app}")
        import traceback
        log.error(traceback.format_exc())

    if not mem_get("profile_household_complete"):
        log.info("👥 Household profile not configured — waiting for normal chat")
    else:
        log.info("👥 Household profile: configured")







    offset = None
    try:
        url_off = f"https://api.telegram.org/bot{CFG['telegram_token']}/getUpdates"
        r_off = requests.get(url_off, params={"offset": -1, "limit": 1}, timeout=10)
        if r_off.status_code == 200:
            results = r_off.json().get("result", [])
            if results:
                offset = results[-1]["update_id"] + 1
                log.info(f"📡 Telegram offset initialized at {offset}")
    except Exception as e_off:
        log.warning(f"⚠️ Telegram offset init: {e_off}")
    log.info("📡 Polling started...")

    while True:
        try:
            updates = telegram_get_updates(offset)

            for update in updates:
                offset = update["update_id"] + 1
                _watchdog["polling_last_update"] = datetime.now()
                if _watchdog["offset_last"] == offset:
                    if _watchdog["offset_blocked_since"] is None:
                        _watchdog["offset_blocked_since"] = datetime.now()
                else:
                    _watchdog["offset_last"] = offset
                    _watchdog["offset_blocked_since"] = None

                if "callback_query" in update:
                    cq = update["callback_query"]
                    chat = str(cq.get("message", {}).get("chat", {}).get("id", ""))
                    if _is_authorized_chat(chat):
                        handle_callback(cq)
                    continue

                if "message" not in update:
                    continue

                msg     = update["message"]
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text   = msg.get("text", "").strip()
                _bind_first_chat(chat_id)

                if not text and "voice" in msg:
                    if _is_authorized_chat(chat_id):
                        try:
                            file_id = msg["voice"].get("file_id")
                            if file_id:
                                telegram_send("🎤 Transcribing...")
                                text = transcribe_voice(file_id)
                                if text:
                                    telegram_send(f"🎤 _{text}_", force=True)
                                else:
                                    telegram_send("❌ Transcription failed.")
                                    continue
                        except Exception as e_voice:
                            log.error(f"Voice: {e_voice}")
                            continue

                if not text or not _is_authorized_chat(chat_id):
                    if text:
                        log.warning(f"⚠️ Unknown chat_id: {chat_id}")
                    continue

                log.info(f"📩 [{chat_id}] {text[:80]}")

                # ═══ WIZARD SETUP (first startup) ═══
                if _wizard_step():
                    if _wizard_handle_message(text):
                        # If wizard just finished, launch first scan
                        if not _wizard_step():
                            try:
                                scan_ha_complete()
                            except Exception:
                                pass
                        continue

                if shared.channel_locked:
                    # /sms → send a new code (even if locked)
                    if text.strip().lower() in ("/sms", "sms"):
                        send_code_sms()
                        telegram_send("📱 New SMS code sent.")
                        continue
                    if check_code(text):
                        telegram_send("✅ Channel unlocked — Hello!")
                    else:
                        telegram_send("🔐 Enter the SMS code (or type /sms to receive a new one)")
                    continue

                response = handle_message(text)
                # User request → response always passes (force=True)
                # Filters only apply to proactive messages
                if len(response) > 4000:
                    for i in range(0, len(response), 4000):
                        telegram_send(response[i:i+4000], force=True)
                else:
                    telegram_send(response, force=True)

            time.sleep(CFG.get("poll_interval_sec", 2))

        except KeyboardInterrupt:
            log.info("🛑 Manual stop")
            telegram_send("🛑 AI Companion stopped")
            break
        except Exception as e:
            log.error(f"❌ Main loop: {e}")
            _watchdog["errors"].append((datetime.now(), str(e)))
            learning_log_failure("main_loop", str(e))
            _watchdog["errors"] = [
                (dt, msg) for dt, msg in _watchdog["errors"]
                if (datetime.now() - dt).total_seconds() < 3600
            ]
            time.sleep(5)


if __name__ == "__main__":
    main()
