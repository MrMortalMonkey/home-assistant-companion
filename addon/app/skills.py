# =============================================================================
# =============================================================================

import json
import logging
import os
import re
import random
import requests
import sqlite3
import time
import threading
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

from shared import *
import shared
import llm_provider

PROFILE_QUESTIONS = [
    {
        "id": "household_people",
        "question": "👥 How many people live in the household?",
        "buttons": [
            {"text": "1", "value": "1"},
            {"text": "2", "value": "2"},
            {"text": "3-4", "value": "3-4"},
            {"text": "5+", "value": "5+"},
        ],
        "skill_key": "household",
    },
    {
        "id": "household_presence",
        "question": "🏠 When is someone usually home on weekdays?",
        "buttons": [
            {"text": "Always (remote work)", "value": "remote_work"},
            {"text": "Morning + evening", "value": "morning_evening"},
            {"text": "Evening only", "value": "evening"},
            {"text": "Variable", "value": "variable"},
        ],
        "skill_key": "household",
    },
    {
        "id": "household_solar",
        "question": "☀️ Do you have solar panels?",
        "buttons": [
            {"text": "Yes", "value": "yes"},
            {"text": "No", "value": "no"},
            {"text": "Planned", "value": "planned"},
        ],
        "skill_key": "household",
    },
    {
        "id": "household_solar_kwc",
        "question": "☀️ What is your installed capacity (kWp)?",
        "condition": lambda profile: profile.get("household_solar") == "yes",
        "buttons": [
            {"text": "< 3 kWp", "value": "<3"},
            {"text": "3-6 kWp", "value": "3-6"},
            {"text": "6-9 kWp", "value": "6-9"},
            {"text": "> 9 kWp", "value": ">9"},
        ],
        "skill_key": "household",
    },
    {
        "id": "household_heating",
        "question": "🌡️ What is your main heating system?",
        "buttons": [
            {"text": "Heat pump", "value": "heat_pump"},
            {"text": "Electric (radiators)", "value": "electric"},
            {"text": "Gas", "value": "gas"},
            {"text": "Other (oil, wood...)", "value": "other"},
        ],
        "skill_key": "household",
    },
    {
        "id": "household_hot_water",
        "question": "🚿 Domestic hot water?",
        "buttons": [
            {"text": "Electric water heater", "value": "electric_tank"},
            {"text": "Heat pump water heater", "value": "heat_pump_water_heater"},
            {"text": "Boiler (gas/oil)", "value": "boiler"},
            {"text": "Solar / Heat pump", "value": "solar_heat_pump"},
        ],
        "skill_key": "household",
    },
    {
        "id": "household_voice_assistant",
        "question": "🗣️ Voice assistant / home automation?",
        "buttons": [
            {"text": "Google Nest/Home", "value": "google"},
            {"text": "Alexa", "value": "alexa"},
            {"text": "Siri / HomeKit", "value": "siri"},
            {"text": "None", "value": "none"},
        ],
        "skill_key": "household",
    },
    {
        "id": "household_goal",
        "question": "🎯 Your main goal?",
        "buttons": [
            {"text": "💰 Reduce the bill", "value": "reduce_bill"},
            {"text": "☀️ Maximize solar", "value": "maximize_solar"},
            {"text": "🔍 Understand my consumption", "value": "understand"},
            {"text": "🤖 Automate everything", "value": "automate"},
        ],
        "skill_key": "household",
    },
]


def _conversational_onboarding_message():
    """Short first-run readiness message."""
    return "Home Assistant AI Companion is online and ready to work."


def _looks_like_conversational_setup(text):
    """Return True when a normal chat message looks like home setup context."""
    t = (text or "").strip().lower()
    if len(t) < 12 or t.startswith("/"):
        return False
    setup_phrases = (
        "i have", "we have", "my home", "our home", "my house", "our house",
        "my electricity", "electricity rate", "power rate", "utility rate",
        "per kwh", "price per kwh", "cost per kwh", "solar", "heat pump",
        "battery", "washer", "washing machine", "dryer", "dishwasher",
        "freezer", "fridge", "ev charger", "water heater",
    )
    monitor_phrases = (
        "monitor", "watch", "track", "alert me", "notify me",
        "keep an eye on", "let me know",
    )
    return any(p in t for p in setup_phrases) or any(p in t for p in monitor_phrases)


def _capture_conversational_setup(text):
    """Store useful plain-English setup notes so future AI replies have context."""
    if not _looks_like_conversational_setup(text):
        return False
    note_text = " ".join((text or "").strip().split())
    if not note_text:
        return False

    data, _ = skill_get("conversational_setup")
    if not isinstance(data, dict):
        data = {}
    notes = data.get("notes", [])
    if not isinstance(notes, list):
        notes = []

    normalized = note_text.lower()
    if any(str(n.get("text", "")).lower() == normalized for n in notes if isinstance(n, dict)):
        return False

    notes.append({
        "text": note_text[:500],
        "created_at": datetime.now().isoformat(),
    })
    data["notes"] = notes[-20:]
    data["updated_at"] = datetime.now().isoformat()
    skill_set("conversational_setup", data)
    _maybe_configure_rate_from_conversation(note_text)
    return True


def _maybe_configure_rate_from_conversation(text):
    """Best-effort extraction of a simple electricity rate from normal chat."""
    t = (text or "").lower()
    rate_hint = any(word in t for word in ("rate", "price", "cost", "electricity", "utility", "per kwh", "/kwh"))
    if not rate_hint:
        return False
    match = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:usd|\$)?\s*(?:per\s*)?(?:/)?\s*kwh", t)
    if not match:
        match = re.search(r"(?:rate|price|cost)\D{0,20}(\d+(?:[\.,]\d+)?)", t)
    if not match:
        return False
    try:
        price = float(match.group(1).replace(",", "."))
    except ValueError:
        return False
    if price <= 0:
        return False
    if price > 1:
        price = price / 100.0
    if price > 1:
        return False

    data = {
        "type": "base",
        "provider": "Conversation",
        "price_kwh": round(price, 6),
        "currency": "USD",
        "source": "conversation",
        "configured_at": datetime.now().isoformat(),
    }
    skill_set("pricing", data)
    log.info(f"⚡ Electricity rate saved from conversation: {price}/kWh")
    return True


VALID_CATEGORIES = [
    "energy_solar",    # APSystems, Anker, micro-inverters
    "energy_heating",  # heat pump, thermostats
    "energy_consumption",      # Ecojoko, meters
    "connected_plug",    # plugs with power measurement
    "weather",              # weather
    "grid_ip",          # device_tracker, nmap
    "grid_zigbee",      # Z2M bridge
    "matter",             # Matter Bridge
    "nas",                # synology
    "printer",         # printer
    "security",           # alarms, cameras
    "multimedia",         # TV, speakers
    "home_appliances",     # home appliances
    "home_assistant_system",         # updates, addons
    "ignore",          # ignored entities
]

PATTERNS_AUTO = [
    ("solarbank_e1600", "state_of_charge",      "energy_battery",   "soc",        "Anker Solarbank Battery"),
    ("solarbank_e1600", "mode",                "energy_battery",   "mode",       "Anker Solarbank mode"),
    ("solarbank_e1600", "power_solar",   "energy_production", "production", "Anker panels W"),
    ("solarbank_e1600", "charge_power", "energy_battery",   "charge",     "Anker charge W"),
    ("solarbank_e1600", "power_output", "energy_production", "injection",  "Anker injection W"),
    ("solarbank_e1600", "discharge_power","energy_battery",  "discharge",   "Anker discharge W"),
    ("system_anker",    "state_of_charge",      "energy_battery",   "soc",        "Anker System SOC"),
    ("system_anker",    "energy_solar",     "energy_production", "production", "Anker system W"),
    ("solar", "production.*now",  "energy_forecast", "realtime",  "Real-time solar forecast"),
    ("solar", "production.*tomorrow",      "energy_forecast", "tomorrow",    "Solar forecast tomorrow"),
    ("weather", "",                      "weather",             "forecast",    "Weather station"),
]

ENTITY_CRITICALITY = {
    "nas":           {"alert_after_h": 2,  "label": "NAS"},
    "grid_zigbee": {"alert_after_h": 2,  "label": "Bridge Zigbee"},
    "matter":        {"alert_after_h": 2,  "label": "Matter Bridge"},
    "energy_solar":{"alert_after_h": 4, "label": "Solar energy"},
    "connected_plug":{"alert_after_h": 24,"label": "Smart plug"},
    "printer":    {"alert_after_h": 24, "label": "Printer"},
    "multimedia":    {"alert_after_h": 48, "label": "Multimedia"},
}

PROVIDERS = {
    "edf": {
        "name": "EDF",
        "offers": {
            "base": {"name": "Rate Blue Base", "type": "base", "price_kwh": 0.2516, "subscription_month": 12.44},
            "hphc": {"name": "Blue peak/off-peak", "type": "hphc", "price_hp": 0.27, "price_hc": 0.2068, "subscription_month": 13.01},
            "tempo": {"name": "Tempo", "type": "tempo", "price_blue_hp": 0.1369, "price_blue_hc": 0.1056,
                       "price_white_hp": 0.1894, "price_white_hc": 0.1486, "price_red_hp": 0.7562, "price_red_hc": 0.1568, "subscription_month": 13.01},
            "zen": {"name": "Green Electric Zen", "type": "hphc", "price_hp": 0.2676, "price_hc": 0.2068, "subscription_month": 12.44},
            "weekend": {"name": "Zen Week-End", "type": "weekend", "price_weekday": 0.2038, "price_weekend": 0.1538, "subscription_month": 14.83},
            "weekend_hphc": {"name": "Zen Week-End peak/off-peak", "type": "weekend_hphc", "price_hp_weekday": 0.2153, "price_hc": 0.1618, "price_weekend": 0.1618, "subscription_month": 15.08},
            "weekend_plus": {"name": "Zen Week-End Plus (Selected day)", "type": "weekend_plus", "price_weekday": 0.2133, "price_weekend_day": 0.1604, "subscription_month": 14.83},
            "weekend_plus_hphc": {"name": "Zen Week-End Plus peak/off-peak (selected day)", "type": "weekend_plus_hphc",
                "price_hp_weekday": 0.2213, "price_hc_weekend_day": 0.166, "subscription_month": 15.08,
                "description": "Peak weekdays | Off-peak + weekend + chosen day + holidays = same reduced price"},
        }
    },
    "totalenergies": {
        "name": "TotalEnergies",
        "offers": {
            "base": {"name": "Essentielle Base", "type": "base", "price_kwh": 0.2516, "subscription_month": 12.44},
            "hphc": {"name": "Essentielle peak/off-peak", "type": "hphc", "price_hp": 0.27, "price_hc": 0.2068, "subscription_month": 13.01},
            "online": {"name": "Online Base", "type": "base", "price_kwh": 0.2177, "subscription_month": 12.44},
        }
    },
    "engie": {
        "name": "Engie",
        "offers": {
            "base": {"name": "Reference Base", "type": "base", "price_kwh": 0.2516, "subscription_month": 12.44},
            "hphc": {"name": "Reference peak/off-peak", "type": "hphc", "price_hp": 0.27, "price_hc": 0.2068, "subscription_month": 13.01},
            "elec_adapt": {"name": "Elec Adapt", "type": "base", "price_kwh": 0.2346, "subscription_month": 12.44},
        }
    },
    "octopus": {
        "name": "Octopus Energy",
        "offers": {
            "base": {"name": "Eco-Consumption Base", "type": "base", "price_kwh": 0.1994, "subscription_month": 12.44},
            "hphc": {"name": "Eco-Consumption peak/off-peak", "type": "hphc", "price_hp": 0.2252, "price_hc": 0.1606, "subscription_month": 13.01},
        }
    },
    "ekwateur": {
        "name": "Ekwateur",
        "offers": {
            "base": {"name": "Electricity Base", "type": "base", "price_kwh": 0.2364, "subscription_month": 12.44},
        }
    },
    "mint": {
        "name": "Mint Energy",
        "offers": {
            "base": {"name": "Classic Base", "type": "base", "price_kwh": 0.2041, "subscription_month": 12.44},
        }
    },
    "other": {
        "name": "Other provider",
        "offers": {
            "custom": {"name": "Custom", "type": "custom"}
        }
    }
}


def cmd_roles():
    """Display discovered roles"""
    roles = role_get_all()
    report = f"🎯 AUTO-DISCOVERED ROLES — {len(roles)}/{len(ROLE_DEFINITIONS)}\n━━━━━━━━━━━━━━━━━━\n"

    for role, definition in ROLE_DEFINITIONS.items():
        details = role_definition_details(definition)
        desc = details.get("description", "Auto-discovered role")
        if role in roles:
            eid = roles[role]["entity_id"]
            conf = roles[role]["confidence"]
            stars = "★" * min(5, int(conf * 5)) + "☆" * (5 - min(5, int(conf * 5)))
            report += f"  ✅ {role}\n    {stars} {eid}\n"
        else:
            report += f"  ❌ {role} — {desc}\n    Not discovered\n"

    no_assignes = len(ROLE_DEFINITIONS) - len(roles)
    if no_assignes > 0:
        report += f"\n⚠️ {no_assignes} unassigned role(s) — /scan to restart discovery"
    else:
        report += f"\n✅ All roles are assigned"

    return report


def _start_profile_button_flow():
    """Legacy household profile button flow."""
    if mem_get("profile_household_complete"):
        return
    if not str(CFG.get("telegram_chat_id", "")).strip():
        log.info("Household profile button flow deferred until Telegram chat_id is known.")
        return

    # Load the current profile
    profile = {}
    try:
        data, _ = skill_get("household")
        if data:
            profile = data
    except Exception:
        pass

    # Find the next unanswered question
    for q in PROFILE_QUESTIONS:
        qid = q["id"]
        if qid in profile:
            continue
        # Check the condition
        if "condition" in q:
            if not q["condition"](profile):
                profile[qid] = "n/a"
                continue
        # Ask the question
        buttons = [
            {"text": b["text"], "callback_data": f"profile:{qid}:{b['value']}"}
            for b in q["buttons"]
        ]
        remaining_count = sum(1 for qq in PROFILE_QUESTIONS if qq["id"] not in profile and qq["id"] != qid)
        msg = f"{q['question']}"
        if remaining_count > 0:
            msg += f"\n({remaining_count} question(s) remaining)"
        telegram_send_buttons(msg, buttons)
        mem_set("profile_household_question", qid)
        return

    # All questions answered
    mem_set("profile_household_complete", "yes")
    skill_set("household", profile)

    # Summary
    labels = {
        "household_people": "👥 People",
        "household_presence": "🏠 Presence",
        "household_solar": "☀️ Solar",
        "household_solar_kwc": "☀️ Capacity",
        "household_heating": "🌡️ Heating",
        "household_hot_water": "🚿 Hot water",
        "household_voice_assistant": "🗣️ Assistant",
        "household_goal": "🎯 Goal",
    }
    msg = "✅ HOUSEHOLD PROFILE SAVED\n━━━━━━━━━━━━━━━━━━\n"
    for qid, label in labels.items():
        val = profile.get(qid, "")
        if val and val != "n/a":
            msg += f"  {label} : {val}\n"
    msg += "\n🧠 These answers improve the assistant's recommendations."
    msg += "\nThe more context it has, the better it can help."
    msg += "\n💡 /profile to review or edit"
    telegram_send(msg)
    log.info(f"✅ Household profile complete: {profile}")


def _parse_numeric_state(value):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _collect_entity_ids(value, found=None):
    """Recursively collect Home Assistant entity IDs from nested API payloads."""
    if found is None:
        found = set()
    if isinstance(value, str):
        if re.match(r"^[a-z_]+\.[A-Za-z0-9_]+$", value):
            found.add(value)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_entity_ids(item, found)
    elif isinstance(value, list):
        for item in value:
            _collect_entity_ids(item, found)
    return found


def _ha_energy_entity_ids():
    """Best-effort read of Home Assistant Energy dashboard references."""
    entity_ids = set()
    for endpoint in ("config/energy", "energy/info"):
        try:
            payload = ha_get(endpoint, _retries=0)
            if payload:
                entity_ids.update(_collect_entity_ids(payload))
        except Exception:
            pass
    return entity_ids


def _looks_like_excluded_energy_source(entity_id, friendly_name):
    combined = f"{entity_id} {friendly_name}".lower()
    excluded = (
        "solar", "pv", "photovoltaic", "battery", "grid", "meter", "mains",
        "import", "export", "return", "tariff", "price", "cost", "forecast",
        "voltage", "current", "humidity", "temperature", "lqi", "linkquality",
    )
    return any(word in combined for word in excluded)


def _measurement_kind(entity):
    attrs = entity.get("attributes", {})
    unit = str(attrs.get("unit_of_measurement", "")).lower()
    device_class = str(attrs.get("device_class", "")).lower()
    state_class = str(attrs.get("state_class", "")).lower()
    if _parse_numeric_state(entity.get("state")) is None:
        return ""
    if device_class == "power" or unit in ("w", "watt", "watts", "kw"):
        return "power"
    if device_class == "energy" or unit in ("wh", "kwh", "mwh"):
        if state_class in ("total", "total_increasing", "measurement", ""):
            return "energy"
    return ""


def _collect_appliance_candidates():
    """Collect power consumers from smart plugs and HA Energy/power sensors."""
    candidates = {}
    states = ha_get("states") or []
    index = {e["entity_id"]: e for e in states}
    energy_ids = _ha_energy_entity_ids()

    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT entity_id, friendly_name FROM entity_map "
            "WHERE category='connected_plug' AND subcategory='power'"
        ).fetchall()
        known_appliances = set(r[0] for r in conn.execute("SELECT entity_id FROM appliances").fetchall())
        ignored = set(r[0] for r in conn.execute("SELECT entity_id FROM entity_map WHERE category='ignore'").fetchall())
        conn.close()
    except Exception:
        rows, known_appliances, ignored = [], set(), set()

    for eid, fname in rows:
        if eid not in known_appliances:
            candidates[eid] = {
                "entity_id": eid,
                "fname": fname or eid,
                "source": "smart_plug",
                "measurement": "power",
            }

    for entity in states:
        eid = entity["entity_id"]
        if eid in candidates or eid in known_appliances or eid in ignored:
            continue
        if not eid.startswith("sensor."):
            continue
        attrs = entity.get("attributes", {})
        fname = attrs.get("friendly_name", eid)
        kind = _measurement_kind(entity)
        if not kind:
            continue
        if _looks_like_excluded_energy_source(eid, fname):
            continue

        from_energy_dashboard = eid in energy_ids
        combined = f"{eid} {fname}".lower()
        appliance_hint = any(
            word in combined
            for word in (
                "washer", "washing", "dryer", "dishwasher", "oven", "freezer",
                "fridge", "refrigerator", "heater", "hvac", "pump", "charger",
                "ev", "kettle", "coffee", "dehumidifier", "air_conditioner",
                "air conditioner", "microwave", "tv", "computer", "server",
            )
        )
        if not (from_energy_dashboard or kind == "power" or appliance_hint):
            continue

        candidates[eid] = {
            "entity_id": eid,
            "fname": fname,
            "source": "ha_energy" if from_energy_dashboard else "power_sensor",
            "measurement": kind,
        }

    source_order = {"smart_plug": 0, "ha_energy": 1, "power_sensor": 2}
    return sorted(candidates.values(), key=lambda c: (source_order.get(c["source"], 9), c["fname"].lower()))


def _start_appliance_button_flow():
    """Legacy appliance button flow."""
    if mem_get("appliances_configured"):
        return

    candidates = _collect_appliance_candidates()
    if not candidates:
        return

    # Store the queue
    mem_set("appliances_queue", json.dumps(candidates))

    # Ask the first question
    _ask_question_appliance_next()


def _ask_question_appliance_next():
    """Ask the next power-consumer identification question."""
    # If waiting for a custom name, do not advance
    if mem_get("pending_name_appliance"):
        return

    queue_json = mem_get("appliances_queue")
    if not queue_json:
        nb_monitored = 0
        try:
            conn_s = sqlite3.connect(DB_PATH)
            nb_monitored = conn_s.execute("SELECT COUNT(*) FROM appliances WHERE monitored=1").fetchone()[0]
            conn_s.close()
        except Exception:
            pass
        mem_set("appliances_configured", "yes")
        telegram_send(
            f"✅ Appliance configuration complete!\n"
            f"📊 {nb_monitored} appliances under monitoring.\n"
            f"Type /monitoring to see everything."
        )
        return

    try:
        queue = json.loads(queue_json)
    except Exception:
        mem_set("appliances_configured", "yes")
        return

    if not queue:
        mem_set("appliances_configured", "yes")
        mem_set("appliances_queue", "")
        return

    item = queue[0]
    eid = item["entity_id"]
    fname = item["fname"]
    source = item.get("source", "power_sensor")
    measurement = item.get("measurement", "power")

    # Clean the name for display
    for suffix in [" Power", " Consumption", " Energy"]:
        fname = fname.replace(suffix, "")

    remaining_count = len(queue) - 1
    if source == "ha_energy":
        source_label = "Home Assistant Energy"
    elif source == "smart_plug":
        source_label = "smart plug power sensor"
    else:
        source_label = "power sensor"

    msg = (
        f"🔌 What does this {source_label} represent?\n"
        f"**{fname}**\n"
        f"`{eid}`\n"
    )
    if measurement == "energy":
        msg += "\nThis looks like a cumulative energy sensor. Choose Monitoring unless you also have a live W power sensor for cycle detection.\n"
    if remaining_count > 0:
        msg += f"\n({remaining_count} item(s) remaining)"

    if measurement == "energy":
        buttons = [
            {"text": "📊 Energy monitor", "callback_data": f"appliance:{eid}:energy_monitor"},
            {"text": "🔌 Other", "callback_data": f"appliance:{eid}:other"},
            {"text": "⬜ Skip", "callback_data": f"appliance:{eid}:ignore"},
        ]
    else:
        buttons = [
            {"text": "🧺 Washing machine", "callback_data": f"appliance:{eid}:washing_machine"},
            {"text": "👕 Dryer", "callback_data": f"appliance:{eid}:dryer"},
            {"text": "🍽️ Dishwasher", "callback_data": f"appliance:{eid}:dishwasher"},
            {"text": "❄️ Freezer", "callback_data": f"appliance:{eid}:freezer"},
            {"text": "🔇 Standby killer", "callback_data": f"appliance:{eid}:standby_killer"},
            {"text": "📊 Monitoring", "callback_data": f"appliance:{eid}:energy_monitor"},
            {"text": "🔌 Other", "callback_data": f"appliance:{eid}:other"},
            {"text": "⬜ Skip", "callback_data": f"appliance:{eid}:ignore"},
        ]
    telegram_send_buttons(msg, buttons)


def _engine_savings_proactive(states, index, now):
    """Engine that actively seeks euros to save.
    Runs every 5 min. 0 tokens. 0 external API.
    Each action = measured euros."""
    # global _eco_proactive_state  # via shared
    hour = now.hour
    minute = now.minute

    _has_solar = role_get("solar_production_w")
    _has_heat_pump = role_get("heat_pump_climate")
    production_w = ha_get_current_solar_production(states) if _has_solar else 0
    price_kwh = rate_current_kwh_price()

    conn = sqlite3.connect(DB_PATH)

    # ═══ 1. MORNING BRIEFING (1x/day between 7h00-7h05) ═══
    # Briefing: 5h Mon-Fri (work), 10h Sat-Sun (rest)
    hour_briefing = 5 if now.weekday() < 5 else 10
    if hour == hour_briefing and minute < 5 and _eco_proactive_state.get("briefing") != now.strftime("%Y-%m-%d"):
        _eco_proactive_state["briefing"] = now.strftime("%Y-%m-%d")

        # Yesterday's savings
        hier = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        eco_hier = conn.execute(
            "SELECT COALESCE(SUM(euros), 0) FROM savings WHERE created_at LIKE ?", (f"{hier}%",)
        ).fetchone()[0]

        # This month's savings
        eco_month = conn.execute(
            "SELECT COALESCE(SUM(euros), 0), COUNT(*) FROM savings WHERE created_at LIKE ?",
            (f"{now.strftime('%Y-%m')}%",)
        ).fetchone()

        # Solar prediction
        solar_prevu = ""
        if _has_solar:
            data_sol, nb_sol = skill_get("window_solar")
            if data_sol and nb_sol >= 20:
                day_str = str(now.weekday())
                if day_str in data_sol:
                    hours = data_sol[day_str]
                    if hours:
                        best_h = max(hours.items(), key=lambda x: x[1][0])
                        solar_prevu = f"\n☀️ Solar peak expected: {best_h[0]}h (~{int(best_h[1][0])}W)"
                        solar_prevu += f"\n💡 Run a machine around {best_h[0]}h for free energy"

        # Standby killers: how many in standby?
        standby_total = 0
        cv_alerts = []
        appliances_cv = conn.execute(
            "SELECT entity_id, custom_name FROM appliances WHERE appliance_type='standby_killer' AND monitored=1"
        ).fetchall()
        for eid_cv, name_cv in appliances_cv:
            switch_eid = eid_cv.replace("sensor.", "switch.").replace("_power", "")
            e_sw = index.get(switch_eid)
            e_se = index.get(eid_cv)
            if e_sw and e_sw.get("state") == "on" and e_se:
                try:
                    w = float(e_se.get("state", 0))
                    if 0 < w < 50:
                        standby_total += w
                        cv_alerts.append(f"{name_cv} ({int(w)}W)")
                except (ValueError, TypeError):
                    pass

        # Adapt the briefing to the household profile
        profile_household, _ = skill_get("household")
        if not profile_household:
            profile_household = {}
        has_assistant = profile_household.get("household_voice_assistant", "none") not in ("none", "n/a")
        name_assistant = profile_household.get("household_voice_assistant", "").title()
        goal = profile_household.get("household_goal", "reduce_bill")

        msg = f"💡 MORNING BRIEFING\n━━━━━━━━━━━━━━━━━━"
        if eco_hier > 0.005:
            msg += f"\n💰 Yesterday: +{eco_hier:.2f}€ saved"
        msg += f"\n📈 This month: {eco_month[0]:.2f}€ ({eco_month[1]} actions)"

        if _has_solar:
            msg += solar_prevu
        
        rate = rate_get()
        if rate.get("type") in ("hphc", "weekend_hphc", "weekend_plus_hphc"):
            hc_started_at = rate.get("hc_started_at", 22)
            hc_ended_at = rate.get("hc_ended_at", 6)
            price_hp = rate.get("price_hp", rate.get("price_hp_weekday", price_kwh))
            price_hc = rate.get("price_hc", rate.get("price_hc_weekend_day", price_kwh))
            delta = price_hp - price_hc
            if delta > 0.01:
                msg += f"\n⚡ Off-peak: {hc_started_at}h-{hc_ended_at}h ({delta*100:.1f}c€/kWh cheaper)"
                if not _has_solar:
                    msg += f"\n💡 Run your machines during off-peak hours"

        if now.weekday() >= 5 and "weekend" in rate.get("type", ""):
            msg += f"\n🗓️ Weekend rate — great day for running machines!"

        # Standby killers
        if cv_alerts:
            cost_standby_day = standby_total * 24 / 1000 * price_kwh
            msg += f"\n\n⚠️ Standby ({int(standby_total)}W = {cost_standby_day:.2f}€/day):"
            for a in cv_alerts:
                msg += f"\n  🔴 {a}"
            if has_assistant:
                msg += f"\n💡 \"{name_assistant}, turn off the TV\""
            else:
                msg += f"\n💡 Switch off the plugs"

        # Personalized goal
        if goal == "understand" and eco_month[1] == 0:
            msg += f"\n\n🔍 Type /energy to see your consumption"
        elif goal == "automate" and has_assistant:
            msg += f"\n\n🤖 Consider automating standby killers via {name_assistant}"

        # ═══ WEATHER ═══
        try:
            weather_parts = []
            # Temperature
            temp_eid = role_get("weather_temperature")
            if temp_eid:
                e_temp = index.get(temp_eid)
                if e_temp and e_temp["state"] not in ("unavailable", "unknown"):
                    weather_parts.append(f"{e_temp['state']}°C")
            # Rain
            rain_entity_id = role_get("weather_rain_chance")
            if rain_entity_id:
                rain_entity = index.get(rain_entity_id)
                if rain_entity and rain_entity["state"] not in ("unavailable", "unknown", "0"):
                    try:
                        pct = int(float(rain_entity["state"]))
                        if pct > 30:
                            weather_parts.append(f"🌧️ rain {pct}%")
                    except (ValueError, TypeError):
                        pass
            # Weather alert
            alert_eid = role_get("weather_alert")
            if alert_eid:
                e_alert = index.get(alert_eid)
                if e_alert and e_alert["state"] not in ("unavailable", "unknown", "Green"):
                    weather_parts.append(f"⚠️ {e_alert['state']}")
            if weather_parts:
                msg += f"\n\n🌤️ Weather: {' | '.join(weather_parts)}"
        except Exception:
            pass

        # ═══ CALENDAR / TRASH ═══
        try:
            headers_cal = {"Authorization": f"Bearer {CFG['ha_token']}"}
            today_start = now.strftime("%Y-%m-%dT00:00:00")
            today_end = now.strftime("%Y-%m-%dT23:59:59")
            tomorrow_start = (now + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
            tomorrow_end = (now + timedelta(days=1)).strftime("%Y-%m-%dT23:59:59")

            r_cals = requests.get(f"{CFG['ha_url']}/api/calendars", headers=headers_cal, verify=False, timeout=10)
            if r_cals.status_code == 200:
                events_today = []
                events_tomorrow = []
                for cal_info in r_cals.json():
                    eid = cal_info.get("entity_id", "")
                    fname = cal_info.get("name", "")
                    # Today
                    try:
                        r_ev = requests.get(
                            f"{CFG['ha_url']}/api/calendars/{eid}?start={today_start}&end={today_end}",
                            headers=headers_cal, verify=False, timeout=5
                        )
                        if r_ev.status_code == 200:
                            for ev in r_ev.json():
                                events_today.append(f"{ev.get('summary', '?')}")
                    except Exception:
                        pass
                    # Tomorrow (especially trash to put out tonight)
                    try:
                        r_ev2 = requests.get(
                            f"{CFG['ha_url']}/api/calendars/{eid}?start={tomorrow_start}&end={tomorrow_end}",
                            headers=headers_cal, verify=False, timeout=5
                        )
                        if r_ev2.status_code == 200:
                            for ev in r_ev2.json():
                                events_tomorrow.append(f"{ev.get('summary', '?')}")
                    except Exception:
                        pass

                if events_today:
                    msg += f"\n\n📅 Today : {', '.join(events_today)}"
                if events_tomorrow:
                    # Identify trash events
                    trash_events = [e for e in events_tomorrow if any(k in e.lower() for k in ("trash", "blue", "green", "yellow", "gray", "recycling", "trash", "recyclable"))]
                    others = [e for e in events_tomorrow if e not in trash_events]
                    if trash_events:
                        msg += f"\n🗑️ Trash tomorrow : {', '.join(trash_events)} — put them out tonight!"
                    if others:
                        msg += f"\n📅 Demain : {', '.join(others)}"
        except Exception:
            pass

        # ═══ WAZE TRAFFIC ═══
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        day_name = days[now.weekday()]
        is_workday = now.weekday() < 5  # Mon-Fri

        if is_workday:
            waze_rortes = []
            for eid in ("sensor.waze_a103", "sensor.waze_rortes_locales", "sensor.waze_travel_time"):
                e_w = index.get(eid)
                if e_w and e_w["state"] not in ("unavailable", "unknown"):
                    try:
                        duration = float(e_w["state"])
                        attrs = e_w.get("attributes", {})
                        route = attrs.get("route", "")
                        distance = attrs.get("distance", "")
                        name = attrs.get("friendly_name", eid.split(".")[-1])
                        dist_str = f" ({distance:.1f}km)" if isinstance(distance, (int, float)) else ""
                        # Find the route short
                        route_short = route.split(";")[0].strip() if route else name
                        waze_rortes.append({"name": route_short, "duration": duration, "dist": dist_str})
                    except (ValueError, TypeError):
                        pass

            if waze_rortes:
                waze_rortes.sort(key=lambda x: x["duration"])
                best = waze_rortes[0]
                msg += f"\n\n🚗 TRAJET"
                for wr in waze_rortes:
                    icon = "🟢" if wr["duration"] < 30 else ("🟡" if wr["duration"] < 45 else "🔴")
                    best_marker = " ← best" if wr == best and len(waze_rortes) > 1 else ""
                    msg += f"\n  {icon} {wr['name']}{wr['dist']} : {int(wr['duration'])} min{best_marker}"
                msg += f"\n\n🚗 Bonne route !"
            else:
                msg += f"\n\n🚗 Have a great day!"
        else:
            msg += f"\n\n🏠 Happy {day_name}!"

        telegram_send(msg)

    # ═══ 2. SOLAR PEAK ALERT (if > 2000W and no appliance running) ═══
    if _has_solar and production_w > 2000:
        has_cycle = any(v == "active" for v in _state_plugs.values())
        last_solar_alert = _eco_proactive_state.get("solar_alert", "")
        if not has_cycle and last_solar_alert != now.strftime("%Y-%m-%d-%H"):
            _eco_proactive_state["solar_alert"] = now.strftime("%Y-%m-%d-%H")
            eco_potential = round(1.5 * price_kwh * (production_w / 2000), 2)
            telegram_send(
                f"☀️ SOLAR PEAK — {int(production_w)}W available !\n"
                f"No machine running.\n"
                f"💰 Start an appliance now → ~{eco_potential:.2f}€ saved"
            )

    # ═══ 3. FORGOTTEN STANDBY (every 2h if switch ON + consumption < 15W) ═══
    if hour >= 8 and hour <= 23:
        for eid_cv, name_cv in (conn.execute(
            "SELECT entity_id, custom_name FROM appliances WHERE appliance_type='standby_killer' AND monitored=1"
        ).fetchall()):
            switch_eid = eid_cv.replace("sensor.", "switch.").replace("_power", "")
            e_sw = index.get(switch_eid)
            e_se = index.get(eid_cv)

            if e_sw and e_sw.get("state") == "on" and e_se:
                try:
                    w = float(e_se.get("state", 0))
                except (ValueError, TypeError):
                    w = 0

                if 0 < w < 15:
                    key = f"standby_{eid_cv}"
                    last = _eco_proactive_state.get(key, "")
                    if last != now.strftime("%Y-%m-%d-%H") and (hour % 2 == 0):
                        _eco_proactive_state[key] = now.strftime("%Y-%m-%d-%H")
                        cost_h = w / 1000 * price_kwh
                        cost_j = cost_h * 24
                        cost_m = cost_j * 30
                        telegram_send(
                            f"🔇 {name_cv} on standby — {int(w)}W\n"
                            f"💸 Cost: {cost_j:.2f}€/day | {cost_m:.1f}€/month\n"
                            f"Turn off the plug to save."
                        )

    # ═══ 4. EVENING SUMMARY (1x/day at 21h00-21h05) ═══
    if hour == 21 and minute < 5 and _eco_proactive_state.get("evening_summary") != now.strftime("%Y-%m-%d"):
        _eco_proactive_state["evening_summary"] = now.strftime("%Y-%m-%d")

        today = now.strftime("%Y-%m-%d")
        eco_day = conn.execute(
            "SELECT type, SUM(euros), COUNT(*) FROM savings WHERE created_at LIKE ? GROUP BY type",
            (f"{today}%",)
        ).fetchall()
        total_day = sum(row[1] for row in eco_day)

        eco_month_total = conn.execute(
            "SELECT COALESCE(SUM(euros), 0) FROM savings WHERE created_at LIKE ?",
            (f"{now.strftime('%Y-%m')}%",)
        ).fetchone()[0]

        if total_day > 0.005 or eco_day:
            type_labels = {
                "cycle_solar": "☀️ Solar",
                "standby_killer": "🔇 Standby avoided",
                "rate_optimal": "⚡ Rate",
            }
            msg = f"📊 DAILY SUMMARY — {today}\n━━━━━━━━━━━━━━━━━━"
            for saving_type, euros, nb in eco_day:
                label = type_labels.get(saving_type, saving_type)
                msg += f"\n  {label} : +{euros:.2f}€ ({nb}x)"
            msg += f"\n━━━\n💰 Today: +{total_day:.2f}€"
            msg += f"\n📈 This month: {eco_month_total:.2f}€"
            telegram_send(msg)

    # ═══ 5. WEEKLY SUMMARY (Sunday 8pm) ═══
    if now.weekday() == 6 and hour == 20 and minute < 5 and _eco_proactive_state.get("summary_hebdo") != now.strftime("%Y-W%W"):
        _eco_proactive_state["summary_hebdo"] = now.strftime("%Y-W%W")

        # Week bornds (Monday 00h → Sunday 23h59)
        monday = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
        sunday_end = now.strftime("%Y-%m-%d") + "T23:59:59"

        # Previous week
        monday_prev = (now - timedelta(days=now.weekday() + 7)).strftime("%Y-%m-%d")
        sunday_prev = (now - timedelta(days=now.weekday() + 1)).strftime("%Y-%m-%d") + "T23:59:59"

        try:
            # Savings this week
            eco_week = conn.execute(
                "SELECT COALESCE(SUM(euros), 0), COUNT(*) FROM savings WHERE created_at >= ?",
                (monday,)
            ).fetchone()
            eco_sem_eur, eco_sem_nb = eco_week

            # Previous week savings
            previous_savings = conn.execute(
                "SELECT COALESCE(SUM(euros), 0) FROM savings WHERE created_at >= ? AND created_at < ?",
                (monday_prev, monday)
            ).fetchone()[0]

            # By type
            eco_by_type = conn.execute(
                "SELECT type, SUM(euros), COUNT(*) FROM savings WHERE created_at >= ? GROUP BY type",
                (monday,)
            ).fetchall()

            # Appliance cycles this week
            cycles_sem = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(consumption_kwh), 0), COALESCE(SUM(saving_eur), 0) FROM appliance_cycles "
                "WHERE ended_at IS NOT NULL AND created_at >= ?",
                (monday,)
            ).fetchone()
            nb_cycles, kwh_cycles, eco_cycles = cycles_sem

            # Intelligence score
            nb_success = conn.execute(
                "SELECT COUNT(*) FROM decisions_log WHERE success=1 AND created_at >= ?",
                (monday,)
            ).fetchone()[0]
            nb_failures = conn.execute(
                "SELECT COUNT(*) FROM decisions_log WHERE success=0 AND created_at >= ?",
                (monday,)
            ).fetchone()[0]

            # This month savings
            eco_month = conn.execute(
                "SELECT COALESCE(SUM(euros), 0) FROM savings WHERE created_at LIKE ?",
                (f"{now.strftime('%Y-%m')}%",)
            ).fetchone()[0]

            # Build the message
            msg = f"📊 SUMMARY HEBDO\n━━━━━━━━━━━━━━━━━━\n"
            msg += f"Week of {monday[5:]} to {now.strftime('%d/%m')}\n\n"

            # Savings
            msg += f"💰 SAVINGS\n"
            msg += f"  This week: +{eco_sem_eur:.2f}€ ({eco_sem_nb} actions)\n"
            if previous_savings > 0:
                delta_pct = ((eco_sem_eur - previous_savings) / previous_savings * 100) if previous_savings > 0.01 else 0
                tendance = "📈" if delta_pct > 5 else ("📉" if delta_pct < -5 else "➡️")
                msg += f"  Previous week: {previous_savings:.2f}€ {tendance} ({delta_pct:+.0f}%)\n"
            msg += f"  This month: {eco_month:.2f}€\n"

            # Breakdown by type
            type_labels = {
                "cycle_solar": "☀️ Solar",
                "standby_killer": "🔇 Standby",
                "rate_optimal": "⚡ Rate",
            }
            if eco_by_type:
                msg += f"\n📋 DETAIL\n"
                for t_eco, eur, nb in eco_by_type:
                    label = type_labels.get(t_eco, t_eco)
                    msg += f"  {label} : +{eur:.2f}€ ({nb}x)\n"

            # Appliances
            if nb_cycles > 0:
                msg += f"\n🔌 MACHINES\n"
                msg += f"  {nb_cycles} cycles | {kwh_cycles:.1f} kWh | {eco_cycles:.2f}€ saved\n"

            # Reliability
            total_decisions = nb_success + nb_failures
            if total_decisions > 0:
                rate = nb_success / total_decisions * 100
                msg += f"\n🛡️ FAIBILITE\n"
                msg += f"  {nb_success}✅ {nb_failures}❌ ({rate:.0f}%)\n"

            # Closing note
            if eco_sem_eur > previous_savings and previous_savings > 0:
                msg += f"\n🎯 Great progress this week!"
            elif eco_sem_eur > 0:
                msg += f"\n💡 Every euro counts."
            else:
                msg += f"\n📊 Baselines are building — next week will be better."

            telegram_send(msg)
        except Exception as ex:
            log.error(f"summary_hebdo: {ex}")

    # ═══ 6. MONTHLY SUMMARY (1st of month at 10h) ═══
    if now.day == 1 and hour == 10 and minute < 5 and _eco_proactive_state.get("summary_month") != now.strftime("%Y-%m"):
        _eco_proactive_state["summary_month"] = now.strftime("%Y-%m")

        previous_month = (now.replace(day=1) - timedelta(days=1))
        month_prec_str = previous_month.strftime("%Y-%m")
        current_month_str = now.strftime("%Y-%m")
        month_2prec = (previous_month.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

        names_month = {1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
                     7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December"}
        month_name = names_month.get(previous_month.month, str(previous_month.month))

        try:
            # ═══ MONTHLY SAVINGS ═══
            eco_month = conn.execute(
                "SELECT COALESCE(SUM(euros), 0), COUNT(*) FROM savings WHERE created_at LIKE ?",
                (f"{month_prec_str}%",)
            ).fetchone()
            eco_by_type = conn.execute(
                "SELECT type, SUM(euros), COUNT(*) FROM savings WHERE created_at LIKE ? GROUP BY type",
                (f"{month_prec_str}%",)
            ).fetchall()

            # Previous month (M-2) for comparison
            eco_m2 = conn.execute(
                "SELECT COALESCE(SUM(euros), 0) FROM savings WHERE created_at LIKE ?",
                (f"{month_2prec}%",)
            ).fetchone()[0]

            # ═══ APPLIANCE CYCLES ═══
            cycles_month = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(consumption_kwh), 0), COALESCE(SUM(saving_eur), 0) "
                "FROM appliance_cycles WHERE ended_at IS NOT NULL AND created_at LIKE ?",
                (f"{month_prec_str}%",)
            ).fetchone()

            # Accumulate daily cost from baselines
            consumption_eid = role_get("consumption_day_kwh")
            cost_entity_id = role_get("consumption_day_cost")

            day_count = previous_month.day  # Last day of previors month
            consumption_kwh_month = 0
            grid_cost_month = 0

            # Method 1: from baselines
            try:
                baselines_consumption = conn.execute(
                    "SELECT AVG(avg_value) FROM baselines WHERE entity_id=?",
                    (consumption_eid,)
                ).fetchone()
                if baselines_consumption and baselines_consumption[0]:
                    consumption_kwh_month = baselines_consumption[0] * day_count
            except Exception:
                pass

            try:
                baselines_eur = conn.execute(
                    "SELECT AVG(avg_value) FROM baselines WHERE entity_id=?",
                    (cost_entity_id,)
                ).fetchone()
                if baselines_eur and baselines_eur[0]:
                    grid_cost_month = baselines_eur[0] * day_count
            except Exception:
                pass

            # If no baselines, estimate from rate
            if grid_cost_month == 0 and consumption_kwh_month > 0:
                grid_cost_month = consumption_kwh_month * rate_current_kwh_price()

            # ═══ SOLAR PRODUCTION ═══
            prod_solar_kwh = 0
            if _has_solar:
                try:
                    prod_eid = role_get("solar_production_kwh")
                    baselines_sol = conn.execute(
                        "SELECT AVG(avg_value) FROM baselines WHERE entity_id=?",
                        (prod_eid,)
                    ).fetchone()
                    if baselines_sol and baselines_sol[0]:
                        prod_solar_kwh = baselines_sol[0] * day_count
                except Exception:
                    pass

            # ═══ BUILD MESSAGE ═══
            msg = f"📊 MONTHLY SUMMARY — {month_name} {previous_month.year}\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

            # Grid consumption
            if consumption_kwh_month > 0 or grid_cost_month > 0:
                msg += f"\n⚡ POWER GRID\n"
                if consumption_kwh_month > 0:
                    msg += f"  Consumption: ~{consumption_kwh_month:.0f} kWh\n"
                if grid_cost_month > 0:
                    msg += f"  Estimated cost: ~{grid_cost_month:.0f}€\n"

            # Solar
            if prod_solar_kwh > 0:
                msg += f"\n☀️ PRODUCTION SOLAR\n"
                msg += f"  Production: ~{prod_solar_kwh:.0f} kWh\n"
                if consumption_kwh_month > 0:
                    total_consumption = consumption_kwh_month + prod_solar_kwh
                    coverage = prod_solar_kwh / total_consumption * 100 if total_consumption > 0 else 0
                    msg += f"  Solar coverage: {coverage:.0f}%\n"
                solar_saving = prod_solar_kwh * rate_current_kwh_price()
                msg += f"  Value produced: ~{solar_saving:.0f}€\n"

            # AI Savings
            msg += f"\n💰 SAVINGS AI\n"
            msg += f"  Total: +{eco_month[0]:.2f}€ ({eco_month[1]} actions)\n"
            if eco_m2 > 0.01:
                delta = ((eco_month[0] - eco_m2) / eco_m2 * 100)
                tendance = "📈" if delta > 5 else ("📉" if delta < -5 else "➡️")
                msg += f"  vs previors month: {tendance} ({delta:+.0f}%)\n"

            type_labels = {
                "cycle_solar": "☀️ Solar",
                "standby_killer": "🔇 Standby",
                "rate_optimal": "⚡ Rate",
            }
            for t_eco, eur, nb in eco_by_type:
                label = type_labels.get(t_eco, t_eco)
                msg += f"  {label} : +{eur:.2f}€ ({nb}x)\n"

            # Appliances
            nb_c, kwh_c, eco_c = cycles_month
            if nb_c > 0:
                msg += f"\n🔌 MACHINES\n"
                msg += f"  {nb_c} cycles | {kwh_c:.1f} kWh | {eco_c:.2f}€ saved\n"

            # Summary
            if grid_cost_month > 0 and eco_month[0] > 0:
                pct_recup = eco_month[0] / grid_cost_month * 100 if grid_cost_month > 0 else 0
                msg += f"\n🎯 AI recovered {pct_recup:.1f}% of your energy bill"

            telegram_send(msg)
        except Exception as ex:
            log.error(f"summary_monthly: {ex}")

    # ═══ 7. APPLIANCE RUNNING DURING PEAK WHEN OFF-PEAK IS NEAR ═══
    rate = rate_get()
    if rate.get("type") in ("hphc", "weekend_hphc", "weekend_plus_hphc"):
        hc_started_at = rate.get("hc_started_at", 22)
        if isinstance(hc_started_at, str):
            try: hc_started_at = int(hc_started_at.split(":")[0])
            except: hc_started_at = 22
        # If 1-2h before off-peak and an appliance is running
        hours_avant_hc = hc_started_at - hour
        if 0 < hours_avant_hc <= 2:
            has_cycle = any(v == "active" for v in _state_plugs.values())
            if has_cycle:
                key_hp = f"hp_alert_{now.strftime('%Y-%m-%d')}"
                if _eco_proactive_state.get(key_hp) != now.strftime("%H"):
                    _eco_proactive_state[key_hp] = now.strftime("%H")
                    price_hp = rate.get("price_hp", rate.get("price_hp_weekday", price_kwh))
                    price_hc = rate.get("price_hc", rate.get("price_hc_weekend_day", price_kwh))
                    delta = price_hp - price_hc
                    if delta > 0.01:
                        eco_possible = delta * 1.5  # ~1.5 kWh per average cycle
                        telegram_send(
                            f"⚡ Appliance running during peak rate!\n"
                            f"Off-peak hours start at {hc_started_at}h.\n"
                            f"💡 Next cycle: start after {hc_started_at}h → ~{eco_possible:.2f}€ saved"
                        )

    conn.close()


def cycle_started_at(entity_id, friendly_name, solar_production_w=0):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('DELETE FROM appliance_cycles WHERE entity_id=? AND ended_at IS NULL', (entity_id,))
    conn.execute('DELETE FROM cycle_measurements WHERE entity_id=?', (entity_id,))
    conn.execute(
        '''INSERT INTO appliance_cycles
           (entity_id, friendly_name, started_at, solar_production_w, created_at)
           VALUES (?, ?, ?, ?, ?)''',
        (entity_id, friendly_name, datetime.now().isoformat(),
         solar_production_w, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def cycle_ended_at(entity_id, consumption_kwh=0.0):
    conn = sqlite3.connect(DB_PATH)
    r = conn.execute(
        'SELECT id, started_at, solar_production_w FROM appliance_cycles WHERE entity_id=? AND ended_at IS NULL',
        (entity_id,)
    ).fetchone()
    if not r:
        conn.close()
        return None
    cycle_id, started_at_str, prod_solar_started_at = r
    started_at = datetime.fromisoformat(started_at_str)
    duration = int((datetime.now() - started_at).total_seconds() / 60)

    # Production solar current
    prod_solar_ended_at = 0
    try:
        states = ha_get("states")
        if states:
            prod_solar_ended_at = ha_get_current_solar_production(states)
    except Exception:
        pass

    # Average solar production during the cycle
    prod_started_at = prod_solar_started_at or 0
    prod_avg = (prod_started_at + prod_solar_ended_at) / 2

    # Average appliance power during the cycle
    power_avg = (consumption_kwh / (duration / 60)) * 1000 if duration > 0 else 0

    if power_avg > 0 and prod_avg > 0:
        coverage_pct = min(100, int(prod_avg / power_avg * 100))
    else:
        coverage_pct = 0

    part_grid = max(0, 100 - coverage_pct) / 100
    price_kwh = rate_current_kwh_price()
    cost_total = round(consumption_kwh * price_kwh, 3)
    cost_grid = round(cost_total * part_grid, 3)
    saving = round(cost_total - cost_grid, 3)

    # Add columns if missing
    try:
        conn.execute("ALTER TABLE appliance_cycles ADD COLUMN saving_eur REAL DEFAULT 0")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE appliance_cycles ADD COLUMN coverage_pct INTEGER DEFAULT 0")
    except Exception:
        pass

    conn.execute(
        '''UPDATE appliance_cycles
           SET ended_at=?, duration_min=?, consumption_kwh=?, cost_eur=?, saving_eur=?, coverage_pct=?, solar_production_w=?
           WHERE id=?''',
        (datetime.now().isoformat(), duration, consumption_kwh, cost_grid, saving, coverage_pct, int(prod_avg), cycle_id)
    )
    conn.commit()
    conn.close()
    try:
        conn2 = sqlite3.connect(DB_PATH)
        conn2.execute(
            "INSERT INTO decisions_log (action, context, result, success, created_at) VALUES (?, ?, ?, 1, ?)",
            ("CYCLE_OK", json.dumps({"eid": entity_id, "kwh": consumption_kwh}, ensure_ascii=False),
             f"{duration}min {consumption_kwh}kWh", datetime.now().isoformat())
        )
        conn2.commit()
        conn2.close()
    except Exception:
        pass

    try:
        samples = _powers_history.get(entity_id, [])
        signature = _calculer_signature_cycle(samples)
        if signature and duration > 10:
            name_prog = _learning_cycle(entity_id, signature, duration, consumption_kwh)
            # Store the signature in the cycle in DB
            try:
                conn3 = sqlite3.connect(DB_PATH)
                try:
                    conn3.execute("ALTER TABLE appliance_cycles ADD COLUMN signature TEXT DEFAULT ''")
                except Exception:
                    pass
                try:
                    conn3.execute("ALTER TABLE appliance_cycles ADD COLUMN program TEXT DEFAULT ''")
                except Exception:
                    pass
                conn3.execute(
                    "UPDATE appliance_cycles SET signature=?, program=? WHERE id=?",
                    (signature, name_prog or "", cycle_id)
                )
                conn3.commit()
                conn3.close()
            except Exception:
                pass
    except Exception as ex:
        log.debug(f"signature cycle: {ex}")

    return {
        "duration_min": duration, "consumption_kwh": consumption_kwh,
        "cost_total": cost_total, "cost_grid": cost_grid,
        "saving": saving, "coverage_pct": coverage_pct,
        "prod_solar_avg": int(prod_avg)
    }


def _calculer_signature_cycle(samples):
    """Calculates a digital ended_atgerprint of a cycle from its power measurements.
    
    The signature encodes the cycle profile: heating, washing, spinning, and pause phases.
    Two cycles of the same program will have very similar signatures.
    
    Method: split the cycle into 5-min slots, classify each slot
    into power levels (L1=0-50W, L2=50-200W, L3=200-500W, P1=>500W, C9=0W pause).
    Signature = concatenation of codes: "C9-L2-L2-P1-L3-L1-L2-P1-L1-L1-C9"
    """
    if not samples or len(samples) < 3:
        return ""

    # Extract watts (samples = [(timestamp, watts), ...]).
    watts = [w for _, w in samples if isinstance(w, (int, float))]
    if len(watts) < 3:
        return ""

    tranche_size = 15
    phases = []
    for i in range(0, len(watts), tranche_size):
        tranche = watts[i:i+tranche_size]
        avg = sum(tranche) / len(tranche)
        if avg < 5:
            phases.append("C9")    # Corpure / pause
        elif avg < 50:
            phases.append("L1")    # Low — standby / ended_at of cycle
        elif avg < 200:
            phases.append("L2")    # Medium — wash / rinse
        elif avg < 500:
            phases.append("L3")    # High — moderate heating
        elif avg < 1000:
            phases.append("P1")    # High power — heating eau / spin
        else:
            phases.append("L6")    # Very powerful — max resistance

    return "-".join(phases)


def _compare_signatures(sig1, sig2):
    """Compare two signatures. Returns a score of similarity 0-100."""
    if not sig1 or not sig2:
        return 0
    p1 = sig1.split("-")
    p2 = sig2.split("-")
    # Align by length (shortst)
    min_len = min(len(p1), len(p2))
    max_len = max(len(p1), len(p2))
    if min_len == 0:
        return 0
    matches = sum(1 for i in range(min_len) if p1[i] == p2[i])
    score = (matches / max_len) * 100
    return int(score)


def _identifier_program(entity_id, signature, duration_min, consumption_kwh):
    """Compare the signature with the programs known. Returns the name or None."""
    programs, _ = skill_get("machine_programs")
    if not programs:
        return None

    progs = programs.get(entity_id, {})
    best_score = 0
    best_name = None

    for name_prog, data_prog in progs.items():
        sig_known = data_prog.get("signature", "")
        score = _compare_signatures(signature, sig_known)
        if score > best_score:
            best_score = score
            best_name = name_prog

    if best_score >= 70:
        return best_name
    return None


def _enregistrer_program(entity_id, program_name, signature, duration_min, consumption_kwh):
    """Record a new program in the skill."""
    programs, _ = skill_get("machine_programs")
    if not programs:
        programs = {}
    if entity_id not in programs:
        programs[entity_id] = {}

    programs[entity_id][program_name] = {
        "signature": signature,
        "duration_avg": duration_min,
        "consumption_avg": consumption_kwh,
        "nb_cycles": 1,
        "last_utilisation": datetime.now().isoformat()
    }
    skill_set("machine_programs", programs)


def _learning_cycle(entity_id, signature, duration_min, consumption_kwh):
    """After a cycle : identifier or ask the name of the program.
    
    - Known program → silent recognition, stats updated
    - Program unknown → buttons Telegram for name
    - The user only sees a question for new programs
    """
    app = appliance_get(entity_id)
    app_name = app["name"] if app and app.get("name") else entity_id

    # Identifier
    recognized_name = _identifier_program(entity_id, signature, duration_min, consumption_kwh)

    if recognized_name:
        programs, _ = skill_get("machine_programs")
        if programs and entity_id in programs and recognized_name in programs[entity_id]:
            prog = programs[entity_id][recognized_name]
            nb = prog.get("nb_cycles", 1)
            # Average glishealth
            prog["duration_avg"] = round((prog["duration_avg"] * nb + duration_min) / (nb + 1), 1)
            prog["consumption_avg"] = round((prog["consumption_avg"] * nb + consumption_kwh) / (nb + 1), 3)
            prog["nb_cycles"] = nb + 1
            prog["last_utilisation"] = datetime.now().isoformat()
            skill_set("machine_programs", programs)
        return recognized_name

    # Program unknown → automatic recording silent
    log.info(f"New cycle {app_name}: {signature[:40]} | {duration_min}min | {consumption_kwh:.2f}kWh")
    return None


def cmd_programs():
    """Show the learned programs for each appliance."""
    programs, _ = skill_get("machine_programs")
    if not programs:
        return "📋 No program learned yet.\nPrograms are learned automatically after each cycle."

    report = "📋 PROGRAMMES APPRIS\n━━━━━━━━━━━━━━━━━━\n"
    for eid, progs in programs.items():
        app = appliance_get(eid)
        app_name = app["name"] if app and app.get("name") else eid
        report += f"\n🔌 {app_name}\n"
        for name, data in progs.items():
            report += f"  📊 {name} : ~{data.get('duration_avg', '?')} min | ~{data.get('consumption_avg', '?')} kWh | {data.get('nb_cycles', 0)} cycles\n"
    
    report += "\n💡 Mention appliance changes in chat so the assistant can adapt."
    return report


def cycle_in_progress(entity_id):
    conn = sqlite3.connect(DB_PATH)
    r = conn.execute(
        'SELECT started_at, solar_production_w FROM appliance_cycles WHERE entity_id=? AND ended_at IS NULL',
        (entity_id,)
    ).fetchone()
    conn.close()
    return r


def generate_energy_graph(states, index):
    """Generate a graph of grid consumption and solar production of the day → bytes PNG."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        log.debug("matplotlib not yet installed — graph unavailable")
        return None

    now = datetime.now()
    conn = sqlite3.connect(DB_PATH)

    today_start = now.strftime("%Y-%m-%dT00:00:00")
    samples = conn.execute(
        "SELECT timestamp, watts FROM cycle_measurements WHERE timestamp > ? ORDER BY timestamp",
        (today_start,)
    ).fetchall()

    consumption_eid = role_get("realtime_consumption")
    baselines_consumption = {}
    if consumption_eid:
        rows = conn.execute(
            "SELECT hour, avg_value FROM baselines WHERE entity_id=? AND weekday=?",
            (consumption_eid, now.weekday())
        ).fetchall()
        baselines_consumption = {h: v for h, v in rows}

    conn.close()

    hours_plugs = {}
    for ts, watts in samples:
        try:
            h = int(ts[11:13])
            if h not in hours_plugs:
                hours_plugs[h] = []
            hours_plugs[h].append(watts)
        except Exception:
            pass

    hours = list(range(0, now.hour + 1))
    consumption_baseline = [baselines_consumption.get(h, 0) for h in hours]
    consumption_plugs = [sum(hours_plugs.get(h, [0])) / max(1, len(hours_plugs.get(h, [1]))) for h in hours]

    # Solar
    solar_data = []
    if role_get("solar_production_w"):
        prod_eid = role_get("solar_production_w")
        if prod_eid:
            conn2 = sqlite3.connect(DB_PATH)
            rows_sol = conn2.execute(
                "SELECT hour, avg_value FROM baselines WHERE entity_id=? AND weekday=?",
                (prod_eid, now.weekday())
            ).fetchall()
            conn2.close()
            sol_dict = {h: v for h, v in rows_sol}
            solar_data = [sol_dict.get(h, 0) for h in hours]

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")

    if consumption_baseline and any(v > 0 for v in consumption_baseline):
        ax.fill_between(hours, consumption_baseline, alpha=0.3, color="#e74c3c", label="Grid consumption (baseline)")
        ax.plot(hours, consumption_baseline, color="#e74c3c", linewidth=2)

    if solar_data and any(v > 0 for v in solar_data):
        ax.fill_between(hours, solar_data, alpha=0.3, color="#f1c40f", label="Solar")
        ax.plot(hours, solar_data, color="#f1c40f", linewidth=2)

    if consumption_plugs and any(v > 0 for v in consumption_plugs):
        ax.bar(hours, consumption_plugs, alpha=0.5, color="#3498db", width=0.6, label="Machines (plugs)")

    ax.set_xlabel("Hour", color="white", fontsize=12)
    ax.set_ylabel("Watts", color="white", fontsize=12)
    ax.set_title(f"⚡ Energy — {now.strftime('%A %d/%m/%Y')}", color="white", fontsize=14, fontweight="bold")
    ax.tick_params(colors="white")
    ax.spines["bottom"].set_color("white")
    ax.spines["left"].set_color("white")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(facecolor="#16213e", edgecolor="white", labelcolor="white", fontsize=10)
    ax.set_xlim(0, 23)
    ax.set_xticks(range(0, 24, 2))

    plt.tight_layort()

    # Convert to bytes
    import io
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _execute_pending_ha_action():
    pending = mem_get("ha_action_pending")
    if not pending:
        return "No Home Assistant action is pending."
    try:
        action = json.loads(pending)
        domain = action["domain"]
        service = action["service"]
        entity_id = action["entity_id"]
        extra_data = action.get("data", {})

        service_data = {"entity_id": entity_id}
        service_data.update(extra_data)
        result = ha_post(f"services/{domain}/{service}", service_data)

        mem_set("ha_action_pending", "")

        if isinstance(entity_id, list):
            entity_short = f"{len(entity_id)} entities"
        else:
            entity_short = entity_id.split(".", 1)[1].replace("_", " ").title() if "." in entity_id else entity_id
        if result is not None:
            log.info(f"✅ HA action: {domain}/{service} on {entity_id}")
            return f"✅ Done: {entity_short}"
        return f"❌ Action failed: {domain}.{service} on {entity_short}"
    except Exception as e:
        log.error(f"❌ HA action error: {e}")
        mem_set("ha_action_pending", "")
        return f"❌ HA action error: {str(e)[:100]}"


def _ha_text_key(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _ha_area_lookup():
    try:
        if shared._entity_areas and shared._areas_id_to_name:
            return {
                eid: shared._areas_id_to_name.get(area_id, area_id)
                for eid, area_id in shared._entity_areas.items()
            }
        entity_areas = ha_get_entity_areas()
        area_names = ha_get_areas_mapping()
        return {eid: area_names.get(area_id, area_id) for eid, area_id in entity_areas.items()}
    except Exception as ex:
        log.debug(f"HA area lookup: {ex}")
        return {}


def _ha_entity_label(entity):
    attrs = entity.get("attributes", {}) if isinstance(entity, dict) else {}
    friendly = attrs.get("friendly_name")
    if friendly:
        return str(friendly)
    eid = str(entity.get("entity_id", "")) if isinstance(entity, dict) else ""
    if "." in eid:
        return eid.split(".", 1)[1].replace("_", " ").title()
    return eid


def _ha_entity_display(entity, area=None):
    """User-facing entity label: Room - Friendly Name (no raw entity_id by default)."""
    label = _ha_entity_label(entity)
    eid = entity.get("entity_id", "") if isinstance(entity, dict) else ""
    if area is None and eid:
        area = ha_get_area(eid)
    if area and area.strip():
        area_txt = str(area).strip()
        if area_txt.lower() not in label.lower():
            return f"{area_txt} - {label}"
    return label


def _ha_find_entities(query, domains=None, states=None, limit=20):
    """Find entities by natural name, area, friendly name, or entity_id."""
    states = states or ha_get("states") or []
    area_by_entity = _ha_area_lookup()
    domains = set(domains or [])
    q_raw = str(query or "").strip()
    q = _ha_text_key(q_raw)
    stop = {
        "the", "a", "an", "please", "can", "you", "would", "will", "turn", "switch",
        "toggle", "set", "to", "at", "on", "off", "entity", "entities", "device",
        "devices", "status", "state", "room", "area", "lights", "light",
    }
    tokens = [t for t in q.split() if t and t not in stop]
    scored = []

    for entity in states:
        eid = entity.get("entity_id", "")
        domain = eid.split(".", 1)[0] if "." in eid else ""
        if domains and domain not in domains:
            continue

        friendly = _ha_entity_label(entity)
        area = area_by_entity.get(eid, "")
        hay = _ha_text_key(f"{eid} {friendly} {area}")
        score = 0

        if q and q in hay:
            score += 60
        if q and _ha_text_key(eid) == q:
            score += 120
        if tokens and all(tok in hay for tok in tokens):
            score += 80
        score += sum(8 for tok in tokens if tok in hay)
        if area and _ha_text_key(area) in q:
            score += 25
        if "light" in q and domain == "light":
            score += 12
        if "gate" in q and domain in ("binary_sensor", "cover", "lock"):
            score += 12
        if "energy" in q and domain == "sensor":
            score += 8

        if score > 0:
            scored.append((score, entity, area))

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:limit]


def _ha_open_states_for_entity(entity_id):
    domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
    if domain == "binary_sensor":
        return {"on", "open", "opened"}
    if domain == "cover":
        return {"open", "opening"}
    if domain == "lock":
        return {"unlocked"}
    return {"on", "open", "opened", "unlocked"}


def _ha_history(entity_id, start_dt=None, end_dt=None):
    start_dt = start_dt or datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = end_dt or datetime.now()
    url = f"{CFG['ha_url']}/api/history/period/{start_dt.isoformat()}"
    headers = {"Authorization": f"Bearer {CFG['ha_token']}"}
    params = {
        "filter_entity_id": entity_id,
        "end_time": end_dt.isoformat(),
        "minimal_response": "",
        "no_attributes": "",
    }
    try:
        r = requests.get(url, headers=headers, params=params, verify=False, timeout=20)
        if r.status_code != 200:
            log.warning(f"HA history {entity_id}: HTTP {r.status_code}")
            return []
        payload = r.json()
        if payload and isinstance(payload, list) and isinstance(payload[0], list):
            return payload[0]
    except Exception as ex:
        log.debug(f"HA history {entity_id}: {ex}")
    return []


def _history_delta_numeric(entity_id):
    rows = _ha_history(entity_id)
    values = []
    for row in rows:
        try:
            values.append(float(str(row.get("state")).replace(",", ".")))
        except Exception:
            continue
    if len(values) < 2:
        return None
    return max(0.0, values[-1] - values[0])


def _ha_confirm_action(domain, service, entity_ids, data=None):
    if isinstance(entity_ids, str):
        entity_payload = entity_ids
    else:
        entity_payload = entity_ids[0] if len(entity_ids) == 1 else entity_ids
    return ha_execute_service_action(domain, service, entity_payload, data or {})


def _ha_queue_watch_request(entity_pattern, condition, state_value, message, cooldown_min):
    payload = {
        "entity_pattern": entity_pattern,
        "condition": condition,
        "state_value": str(state_value or ""),
        "message": message or "",
        "cooldown_min": int(cooldown_min or 60),
    }
    mem_set("ha_watch_pending", json.dumps(payload))
    preview = [
        "Confirm monitor?",
        f"Target: {entity_pattern}",
        f"Condition: {condition}{' ' + str(state_value) if state_value else ''}",
        f"Cooldown: {int(cooldown_min or 60)} min",
    ]
    if message:
        preview.append(f"Alert: {message}")
    telegram_send_buttons(
        "\n".join(preview),
        [
            {"text": "✅ Confirm", "callback_data": "ha_watch:confirm"},
            {"text": "❌ Cancel", "callback_data": "ha_watch:cancel"},
        ],
    )
    return ""


def _ha_direct_action(text):
    t = text.strip()
    low = t.lower().strip().rstrip(".")
    match = re.match(r"^(turn|switch)\s+(on|off)\s+(.+)$", low)
    if not match:
        match = re.match(r"^(toggle)\s+(.+)$", low)
        if not match:
            return None
        action = "toggle"
        target = match.group(2)
    else:
        action = match.group(2)
        target = match.group(3)

    brightness = None
    bright_match = re.search(r"\b(?:to|at)\s+(\d{1,3})\s*%?\b", target)
    if bright_match:
        brightness = max(1, min(100, int(bright_match.group(1))))
        target = target[:bright_match.start()].strip()

    domains = ["light"] if "light" in target else ["light", "switch", "fan", "cover", "lock"]
    matches = _ha_find_entities(target, domains=domains, limit=12)
    if not matches:
        return None

    best_score = matches[0][0]
    selected = [entity for score, entity, _ in matches if score >= max(50, best_score - 10)]
    if "light" in target:
        selected = [e for e in selected if e["entity_id"].startswith("light.")]
    if not selected:
        selected = [matches[0][1]]

    domain = selected[0]["entity_id"].split(".", 1)[0]
    service = {"on": "turn_on", "off": "turn_off", "toggle": "toggle"}[action]
    data = {}
    if brightness and domain == "light" and service == "turn_on":
        data["brightness_pct"] = brightness
    return _ha_confirm_action(domain, service, [e["entity_id"] for e in selected], data)


def _ha_offline_entities_response(text):
    low = (text or "").lower()
    if not any(k in low for k in ("offline", "unavailable", "down", "not responding")):
        return None

    states = ha_get("states") or []
    offline_states = {"unavailable", "unknown", "offline"}
    rows = []
    for entity in states:
        state = str(entity.get("state", "")).lower()
        if state not in offline_states:
            continue
        eid = entity.get("entity_id", "")
        attrs = entity.get("attributes", {})
        friendly = attrs.get("friendly_name", eid)
        room = ha_get_area(eid)
        text_blob = _ha_text_key(f"{eid} {friendly} {room}")
        if "zigbee" in low and not any(k in text_blob for k in ("zigbee", "z2m", "zha")):
            continue
        rows.append((entity, room, state))

    if not rows:
        if "zigbee" in low:
            return "I do not see any offline Zigbee entities right now."
        return "I do not see any offline entities right now."

    if any(k in low for k in ("how many", "count")):
        if "zigbee" in low:
            return f"{len(rows)} Zigbee entities are currently offline."
        return f"{len(rows)} entities are currently offline."

    rows.sort(key=lambda r: r[0].get("entity_id", ""))
    title = "Offline Zigbee entities" if "zigbee" in low else "Offline entities"
    lines = [f"{title}: {len(rows)}"]
    for entity, room, state in rows[:40]:
        lines.append(f"- {_ha_entity_display(entity, room)} = {state}")
    if len(rows) > 40:
        lines.append(f"...and {len(rows) - 40} more.")
    return "\n".join(lines)


def _ha_entities_in_area_response(text):
    low = text.lower()
    if "entities" not in low and "devices" not in low:
        return None
    if ("offline" in low or "unavailable" in low or "zigbee" in low) and " in " not in low:
        return None
    area_match = re.search(r"\b(?:in|inside)\s+(?:the\s+)?(.+?)(?:\?|$)", low)
    if area_match:
        area_query = area_match.group(1).strip()
    else:
        before_entities = re.search(r"\bwhat\s+(.+?)\s+(?:entities|devices)\b", low)
        if before_entities:
            area_query = before_entities.group(1).strip()
        else:
            area_query = low
            for phrase in ("what", "entities", "devices", "can you see", "do you see", "are there"):
                area_query = area_query.replace(phrase, "")
            area_query = area_query.strip()
    area_query = area_query.strip(" ?.")
    if not area_query:
        return None

    states = ha_get("states") or []
    area_by_entity = _ha_area_lookup()
    area_key = _ha_text_key(area_query)
    rows = []
    for entity in states:
        eid = entity.get("entity_id", "")
        area = area_by_entity.get(eid, "")
        if area and area_key in _ha_text_key(area):
            unit = entity.get("attributes", {}).get("unit_of_measurement", "")
            value = f"{entity.get('state', '?')} {unit}".strip()
            rows.append((entity, area, value))

    if not rows:
        return f"I could not find entities in an area matching '{area_query}'."
    rows.sort(key=lambda item: item[0].get("entity_id", ""))
    lines = [f"I can see {len(rows)} entities in {area_query.title()}:"]
    for entity, area, value in rows[:30]:
        lines.append(f"- {_ha_entity_display(entity, area)}: {value}")
    if len(rows) > 30:
        lines.append(f"...and {len(rows) - 30} more.")
    return "\n".join(lines)


def _ha_open_count_today_response(text):
    low = text.lower()
    if "how many times" not in low or "open" not in low or "today" not in low:
        return None
    target = low
    target = re.sub(r"how many times (?:was|did|has|have)?", "", target)
    target = target.replace("opened today", "").replace("open today", "").replace("was", "")
    target = target.strip(" ?.")
    matches = _ha_find_entities(target, domains=["binary_sensor", "cover", "lock"], limit=5)
    if not matches:
        return f"I could not find a gate/door entity matching '{target}'."
    entity = matches[0][1]
    eid = entity["entity_id"]
    open_states = _ha_open_states_for_entity(eid)
    rows = _ha_history(eid)
    count = 0
    previous_open = False
    for row in rows:
        state_open = str(row.get("state", "")).lower() in open_states
        if state_open and not previous_open:
            count += 1
        previous_open = state_open
    current = entity.get("state", "?")
    return f"{_ha_entity_display(entity)} opened {count} time(s) today. Current state: {current}."


def _ha_energy_today_response(text):
    low = text.lower()
    if "energy" not in low or "today" not in low:
        return None
    target = low
    target = re.sub(r"how much energy (?:did|has|have)?", "", target)
    target = target.replace("use today", "").replace("used today", "").replace("usage today", "")
    target = target.replace("current daily energy usage", "energy").strip(" ?.")
    states = ha_get("states") or []
    candidates = []
    for score, entity, area in _ha_find_entities(target, domains=["sensor"], states=states, limit=25):
        attrs = entity.get("attributes", {})
        unit = str(attrs.get("unit_of_measurement", "")).lower()
        device_class = str(attrs.get("device_class", "")).lower()
        name_key = _ha_text_key(f"{entity.get('entity_id')} {_ha_entity_label(entity)}")
        if device_class == "energy" or unit in ("kwh", "wh", "mwh"):
            if not any(word in name_key for word in ("cost", "price", "voltage", "current")):
                candidates.append((score, entity, unit))
    if not candidates:
        return f"I could not find a matching energy sensor for '{target}'."

    candidates.sort(key=lambda item: item[0], reverse=True)
    entity = candidates[0][1]
    unit = candidates[0][2] or str(entity.get("attributes", {}).get("unit_of_measurement", ""))
    delta = _history_delta_numeric(entity["entity_id"])
    if delta is None:
        return f"I found {_ha_entity_display(entity)}, but Home Assistant history did not return enough data for today."
    display_delta = delta / 1000 if unit == "wh" else delta
    display_unit = "kWh" if unit == "wh" else (unit or "kWh")
    return f"{_ha_entity_display(entity)} used {display_delta:.2f} {display_unit} today."


def _ha_open_too_long_watch(text):
    low = text.lower()
    if not any(word in low for word in ("notify", "alert", "tell me")):
        return None
    if "open" not in low or not any(phrase in low for phrase in ("too long", "longer than", "more than", "over ")):
        return None
    duration = 10
    match = re.search(r"(\d+)\s*(minute|minutes|min|hour|hours|hr|hrs)", low)
    if match:
        duration = int(match.group(1))
        if match.group(2).startswith(("hour", "hr")):
            duration *= 60
    target = low
    target = re.sub(r"^(notify|alert|tell)\s+me\s+(if|when)?", "", target).strip()
    target = re.sub(r"\b(is|was|stays|stayed|has been|for|more than|longer than|over|too long|open|opened)\b", " ", target)
    target = re.sub(r"\d+\s*(minute|minutes|min|hour|hours|hr|hrs)", " ", target).strip()
    matches = _ha_find_entities(target, domains=["binary_sensor", "cover", "lock"], limit=5)
    if not matches:
        return f"I could not find a gate/door entity matching '{target}'."
    entity = matches[0][1]
    eid = entity["entity_id"]
    message = f"{_ha_entity_label(entity)} has been open for {duration} minutes."
    _ha_queue_watch_request(eid, "open_for", str(duration), message, max(duration, 30))
    return ""


def _ha_native_response(text):
    for handler in (
        _ha_direct_action,
        _ha_offline_entities_response,
        _ha_entities_in_area_response,
        _ha_open_count_today_response,
        _ha_energy_today_response,
        _ha_open_too_long_watch,
    ):
        try:
            response = handler(text)
            if response is not None:
                return response
        except Exception as ex:
            log.debug(f"{handler.__name__}: {ex}")
    return None


def handle_callback(callback_query):
    cqid = callback_query.get("id")
    data = callback_query.get("data", "")
    chat = str(callback_query.get("message", {}).get("chat", {}).get("id", ""))

    if not _is_authorized_chat(chat):
        return

    telegram_answer_callback(cqid)

    if shared.channel_locked:
        if not data.startswith("wizard_"):
            telegram_send("🔐 Channel locked — enter the SMS code first.")
            return

    if data == "auto_confirm":
        pending = mem_get("ha_automation_pending")
        if not pending:
            telegram_send("⚠️ No automation pending (already confirmed or cancelled).")
            return
        try:
            auto_data = json.loads(pending)
            alias = auto_data.get("alias", "AI Companion Auto")
            auto_id = alias.lower().replace(" ", "_").replace("-", "_").replace("e", "e").replace("e", "e").replace("a", "a")[:40]

            existing = ha_get(f"states/automation.{auto_id}")
            if existing and existing.get("state") in ("on", "off"):
                telegram_send(f"⚠️ Automation '{alias}' already exists. Delete it in HA first or request a modification.")
                mem_set("ha_automation_pending", "")
                return

            mem_set("ha_config_write_consent", "yes")
            try:
                result = ha_post(f"config/automation/config/{auto_id}", auto_data)
            finally:
                mem_set("ha_config_write_consent", "")
            mem_set("ha_automation_pending", "")
            if result is not None:
                telegram_send(f"✅ Automation created: {alias}")
            else:
                telegram_send("❌ Error creating HA automation")
        except Exception as e:
            telegram_send(f"❌ Error: {e}")
            log.error(f"Auto confirm: {e}")
        return

    if data == "auto_modify":
        pending = mem_get("ha_automation_pending")
        if pending:
            mem_set("ha_automation_modify", "yes")
            telegram_send("✏️ Describe the desired changes.\nExample: \"Change the threshold to 90% instead of 100%\"")
        else:
            telegram_send("⚠️ No automation pending.")
        return

    if data == "auto_cancel":
        mem_set("ha_automation_pending", "")
        telegram_send("❌ Automation cancelled.")
        return

    if data == "ha_action:confirm":
        telegram_send("ℹ️ Runtime actions now execute immediately. No pending action to confirm.")
        return

    if data == "ha_action:cancel":
        mem_set("ha_action_pending", "")
        telegram_send("ℹ️ Runtime actions now execute immediately.")
        return

    if data == "ha_watch:confirm":
        pending_watch = mem_get("ha_watch_pending", "")
        if not pending_watch:
            telegram_send("⚠️ No monitor request is pending.")
            return
        try:
            watch = json.loads(pending_watch)
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "INSERT INTO watches (entity_pattern, condition, state_value, message, cooldown_min, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    watch.get("entity_pattern", ""),
                    watch.get("condition", ""),
                    str(watch.get("state_value", "")),
                    watch.get("message", ""),
                    int(watch.get("cooldown_min", 60)),
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
            conn.close()
            mem_set("ha_watch_pending", "")
            telegram_send("✅ Monitoring rule created.")
        except Exception as ex:
            log.error(f"Watch confirm: {ex}")
            telegram_send(f"❌ Monitor creation failed: {str(ex)[:120]}")
        return

    if data == "ha_watch:cancel":
        mem_set("ha_watch_pending", "")
        telegram_send("❌ Monitor creation cancelled.")
        return

    # ═══ WIZARD CALLBACKS ═══
    if data.startswith("wizard_sms:"):
        method = data.split(":", 1)[1]
        if method == "free_mobile":
            CFG["_wizard_step"] = "sms_free_user"
            _wizard_save_config()
            telegram_send(
                "📱 FREE MOBILE\n"
                "━━━━━━━━━━━━━━\n"
                "Enable the option in your Subscriber Area:\n"
                "My options -> SMS notifications\n\n"
                "Send your Free Mobile username (8 digits):"
            )
        elif method == "ha_notify":
            CFG["_wizard_step"] = "sms_ha_notify_service"
            _wizard_save_config()
            # List available notify services
            notify_list = []
            try:
                headers = {"Authorization": f"Bearer {CFG['ha_token']}"}
                r = requests.get(f"{CFG['ha_url']}/api/services", headers=headers, verify=False, timeout=10)
                if r.status_code == 200:
                    for s in r.json():
                        if s.get("domain") == "notify":
                            notify_list = list(s.get("services", {}).keys())
            except Exception:
                pass
            msg = "🔔 NOTIFICATION HA COMPANION\n━━━━━━━━━━━━━━\n"
            msg += "The HA Companion app on your phone will receive the code.\n\n"
            if notify_list:
                mobile_apps = [n for n in notify_list if "mobile_app" in n]
                if mobile_apps:
                    msg += "Detected services:\n" + "\n".join(f"  • {n}" for n in mobile_apps[:5])
                else:
                    msg += "Services notify :\n" + "\n".join(f"  • {n}" for n in notify_list[:5])
                msg += "\n\nEnvoyez the name of the service :"
            else:
                msg += "Send the notify service name (e.g.: mobile_app_my_iphone):"
            telegram_send(msg)
        elif method == "email":
            CFG["_wizard_step"] = "sms_email_addr"
            _wizard_save_config()
            telegram_send(
                "📧 EMAIL\n"
                "━━━━━━━━━━━━━━\n"
                "The code will be sent by email.\n\n"
                "Send your email address:"
            )
        return

    if data.startswith("profile:"):
        parts = data.split(":", 2)
        if len(parts) == 3:
            _, qid, value = parts
            profile = {}
            try:
                d, _ = skill_get("household")
                if d:
                    profile = d
            except Exception:
                pass
            profile[qid] = value
            skill_set("household", profile)
            # Confirmation short
            q_label = next((q["question"].split("\n")[0] for q in PROFILE_QUESTIONS if q["id"] == qid), qid)
            v_label = next((b["text"] for q in PROFILE_QUESTIONS if q["id"] == qid for b in q["buttons"] if b["value"] == value), value)
            telegram_send(f"✅ {v_label}")
            # Next question
            _start_profile_button_flow()
        return

    if data.startswith("prog_name:"):
        parts = data.split(":", 4)
        if len(parts) >= 5:
            eid = parts[1]
            sig_short = parts[2]
            duration = int(parts[3]) if parts[3].isdigit() else 0
            consumption = float(parts[4]) if parts[4].replace(".", "").isdigit() else 0
            # Store as pending and ask the name
            mem_set("pending_program_name", json.dumps({
                "entity_id": eid, "signature": sig_short, "duration": duration, "consumption": consumption
            }))
            app = appliance_get(eid)
            app_name = app["name"] if app and app.get("name") else eid
            telegram_send(
                f"📝 Enter the program name for {app_name}\n"
                f"Examples: Cotton 60°, Synthetic 40°, Express, Eco...",
                force=True
            )
        return

    if data.startswith("prog_ignore:"):
        telegram_send("✅ Program skipped.", force=True)
        return

    if data.startswith("cycle_ended_at:"):
        eid = data.split(":", 1)[1]
        app = appliance_get(eid)
        app_name = app["name"] if app and app.get("name") else eid
        _state_plugs.pop(eid, None)
        # Close the cycle in the database
        try:
            conn_cf = sqlite3.connect(DB_PATH)
            conn_cf.execute(
                "UPDATE appliance_cycles SET ended_at=? WHERE entity_id=? AND ended_at IS NULL",
                (datetime.now().isoformat(), eid)
            )
            conn_cf.execute("DELETE FROM cycle_measurements WHERE entity_id=?", (eid,))
            conn_cf.commit()
            conn_cf.close()
        except Exception:
            pass
        telegram_send(f"✅ {app_name} — cycle closed. No duplicate notification.", force=True)
        return

    if data.startswith("cycle_continue:"):
        eid = data.split(":", 1)[1]
        app = appliance_get(eid)
        app_name = app["name"] if app and app.get("name") else eid
        _state_plugs[eid] = "active"
        # Restore the samples
        try:
            conn_cc = sqlite3.connect(DB_PATH)
            rows = conn_cc.execute(
                "SELECT ts, watts FROM cycle_measurements WHERE entity_id=? ORDER BY ts", (eid,)
            ).fetchall()
            _powers_history[eid] = [(ts, w) for ts, w in rows]
            conn_cc.close()
        except Exception:
            pass
        telegram_send(f"🔄 {app_name} — cycle summaryd. I continue monitoring.", force=True)
        return

    if data.startswith("cmd:"):
        cmd_name = data.split(":", 1)[1].strip()
        try:
            response = handle_message(cmd_name)
            if response:
                telegram_send(response, force=True)
        except Exception:
            pass
        return

    if data.startswith("appliance:"):
        parts = data.split(":", 2)
        if len(parts) == 3:
            _, eid, type_app = parts
            fname_clean = eid.replace("sensor.", "").replace("_power", "").replace("_", " ").title()

            if type_app == "other":
                mem_set("pending_name_appliance", eid)
                telegram_send(
                    f"🔌 {fname_clean}\n"
                    f"Which appliance is this ? Send the name :\n"
                    f"(e.g.: Garage freezer, Oven, Coffee maker, Desktop PC...)"
                )
                return

            if type_app == "ignore":
                appliance_set(eid, "ignore", "⬜ Ignored")
                nb_monitored = 0
                try:
                    conn_nb = sqlite3.connect(DB_PATH)
                    nb_monitored = conn_nb.execute("SELECT COUNT(*) FROM appliances WHERE monitored=1").fetchone()[0]
                    conn_nb.close()
                except Exception:
                    pass
                telegram_send(
                    f"⬜ {fname_clean} — parked\n"
                    f"No tracking, no notification.\n"
                    f"(mention appliance changes in chat to update monitoring)\n"
                    f"📊 {nb_monitored} appliances monitored"
                )
            else:
                label = APPLIANCE_TYPES.get(type_app, type_app)
                appliance_set(eid, type_app, label)
                nb_monitored = 0
                try:
                    conn_nb = sqlite3.connect(DB_PATH)
                    nb_monitored = conn_nb.execute("SELECT COUNT(*) FROM appliances WHERE monitored=1").fetchone()[0]
                    conn_nb.close()
                except Exception:
                    pass

                if type_app == "standby_killer":
                    telegram_send(
                        f"🔇 {fname_clean} → Cut standby\n"
                        f"Standby savings measured automatically.\n"
                        f"Every hour OFF = watts avoided → ROI counter.\n"
                        f"📊 {nb_monitored} appliances monitored"
                    )
                elif type_app == "energy_monitor":
                    telegram_send(
                        f"📊 {fname_clean} → Energy monitoring\n"
                        f"Measures production/consumption — no cycle detection.\n"
                        f"📊 {nb_monitored} appliances monitored"
                    )
                else:
                    telegram_send(
                        f"✅ {fname_clean} → {label}\n"
                        f"Monitoring active — cycles, costs, savings.\n"
                        f"📊 {nb_monitored} appliances monitored"
                    )

            # Next question
            try:
                queue = json.loads(mem_get("appliances_queue") or "[]")
                queue = [q for q in queue if q["entity_id"] != eid]
                mem_set("appliances_queue", json.dumps(queue))
                _ask_question_appliance_next()
            except Exception:
                pass
        return

    if data.startswith("room:"):
        parts = data.split(":", 2)
        if len(parts) == 3:
            _, room, entity_id = parts
            conn = sqlite3.connect(DB_PATH)
            conn.execute('UPDATE entity_map SET room=? WHERE entity_id=?', (room, entity_id))
            conn.commit()
            conn.close()
            telegram_send(f"✅ {entity_id}\nRoom: {room}")
        return

    # Zigbee Normal/Abnormal : zigbee_normal:entity_id or zigbee_abnormal:entity_id
    if data.startswith("zigbee_normal:"):
        entity_id = data.split(":", 1)[1]
        zigbee_absence_status(entity_id, "normal")
        telegram_send(f"✅ Noted — {entity_id}\nNo more alerts for this device being temporarily offline.")
        return

    if data.startswith("zigbee_abnormal:"):
        entity_id = data.split(":", 1)[1]
        zigbee_absence_status(entity_id, "abnormal")
        telegram_send(f"🔍 Monitoring activated — {entity_id}\nAlert when back online or after 2h.")
        return

    if data.startswith("entity_ori:"):
        entity_id = data.split(":", 1)[1]
        conn = sqlite3.connect(DB_PATH)
        already = conn.execute(
            "SELECT response FROM pending_entities WHERE entity_id=?", (entity_id,)
        ).fetchone()
        if already and already[0]:
            conn.close()
            return  # Already processed — duplicate callback ignored
        row = conn.execute(
            "SELECT proposed_category, friendly_name FROM pending_entities WHERE entity_id=?",
            (entity_id,)
        ).fetchone()
        if row:
            cat, fname = row
            room = ha_get_area(entity_id)
            conn.execute(
                """INSERT OR REPLACE INTO entity_map
                   (entity_id, category, subcategory, room, friendly_name, learned_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (entity_id, cat, "", room, fname, datetime.now().isoformat())
            )
            conn.execute(
                "UPDATE pending_entities SET response='yes' WHERE entity_id=?",
                (entity_id,)
            )
            conn.commit()
            conn.close()
            msg = f"✅ Integrated — **{fname}**\nCategory: {cat}\n"
            if cat in ("energy_battery", "energy_production", "energy_forecast"):
                msg += "🔋 Integrated into the Energy group — I will optimize your grid consumption."
            telegram_send_buttons(msg, [
                {"text": "↩️ Cancel this integration", "callback_data": f"entity_cancel:{entity_id}"},
            ])
            log.info(f"✅ Entity validated by user: {entity_id} → {cat}")
        else:
            conn.close()
            telegram_send("✅ Noted.")
        return

    if data.startswith("entity_cancel:"):
        entity_id = data.split(":", 1)[1]
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT friendly_name, response FROM pending_entities WHERE entity_id=?",
            (entity_id,)
        ).fetchone()
        if row:
            fname, response_precedente = row
            # Put back as pending
            conn.execute(
                "UPDATE pending_entities SET response=NULL, question_asked=0 WHERE entity_id=?",
                (entity_id,)
            )
            if response_precedente == "yes":
                conn.execute(
                    "DELETE FROM entity_map WHERE entity_id=?",
                    (entity_id,)
                )
                conn.commit()
                conn.close()
                telegram_send(
                    f"↩️ Cancelled — **{fname}** removed from Energy group.\n"
                    f"It will be re-proposed on the next scan."
                )
            else:
                conn.commit()
                conn.close()
                telegram_send(
                    f"↩️ Cancelled — **{fname}** put back on hold.\n"
                    f"It will be re-proposed on the next scan."
                )
            log.info(f"↩️ Entity cancelled: {entity_id} (was: {response_precedente})")
        else:
            conn.close()
            telegram_send("↩️ Cancellation failed — entity not found in memory.")
        return

    if data.startswith("entity_no:"):
        entity_id = data.split(":", 1)[1]
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT friendly_name FROM pending_entities WHERE entity_id=?",
            (entity_id,)
        ).fetchone()
        fname = row[0] if row else entity_id
        conn.execute(
            "UPDATE pending_entities SET response='no' WHERE entity_id=?",
            (entity_id,)
        )
        conn.execute(
            """INSERT OR REPLACE INTO entity_map
               (entity_id, category, subcategory, room, friendly_name, learned_at)
               VALUES (?, 'ignore', '', '', ?, ?)""",
            (entity_id, fname, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        telegram_send_buttons(f"❌ Ignored — {fname}\nContinuing search.", [
                {"text": "↩️ Cancel — re-integrate", "callback_data": f"entity_cancel:{entity_id}"},
            ])
        log.info(f"❌ Entity ignored by user: {entity_id}")
        return

    if data.startswith("missing_entity_ok:"):
        entity_id = data.split(":", 1)[1]
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "UPDATE entity_map SET category='ignore' WHERE entity_id=?",
            (entity_id,)
        )
        conn.commit()
        conn.close()
        telegram_send(f"✅ Noted — {entity_id} removed from monitoring.\nNo more alerts for this entity.")
        log.info(f"✅ Confirmed missing entity removed: {entity_id}")
        return

    if data.startswith("missing_entity_ko:"):
        entity_id = data.split(":", 1)[1]
        # Reset the alert so it can return in 4h
        mem_set(f"missing_entity_{entity_id}", "")
        telegram_send(
            f"🔍 Monitoring activated — {entity_id}\n"
            f"Alert if still missing at the next scan."
        )
        log.warning(f"❌ Abnormal missing entity: {entity_id}")
        return

    # Auto-correction: apply or cancel the AI-generated patch
    if data.startswith("patch_apply:"):
        old_code = mem_get("patch_pending_old", "")
        new_code = mem_get("patch_pending_new", "")
        explanation = mem_get("patch_pending_expl", "")
        if not old_code:
            telegram_send("⚠️ No patch pending")
            return
        try:
            cfg_secret = CFG.get("deploy_secret", "")
            patch_body = json.dumps({"mode": "replace", "old_str": old_code, "new_str": new_code}).encode()
            sig = hmac.new(cfg_secret.encode(), patch_body, hashlib.sha256).hexdigest()
            req_patch = urllib.request.Request("http://localhost:8501/deploy", data=patch_body, method="POST")
            req_patch.add_header("Authorization", f"HMAC {sig}")
            req_patch.add_header("Content-Type", "application/json")
            resp_patch = urllib.request.urlopen(req_patch, timeout=30)
            result = json.loads(resp_patch.read().decode())
            if result.get("status") == "ok":
                telegram_send(
                    f"✅ PATCH APPLIQUE + REDEMARRE\n"
                    f"Correction : {explanation}\n"
                    f"Backup : {result.get('patch', {}).get('backup', '?')}"
                )
            else:
                telegram_send(f"❌ Failure deploy : {result.get('message', result)}")
        except Exception as e:
            telegram_send(f"❌ Error deploy : {e}")
        finally:
            mem_set("patch_pending_old", "")
            mem_set("patch_pending_new", "")
        return

    if data.startswith("patch_cancel:"):
        mem_set("patch_pending_old", "")
        mem_set("patch_pending_new", "")
        telegram_send("❌ Patch cancelled — no changes.")
        return

    # Power outage: restore exact pre-outage state
    if data.startswith("outage_restore:"):
        snapshot_json = mem_get("outage_snapshot", "{}")
        try:
            snapshot = json.loads(snapshot_json)
        except Exception:
            snapshot = {}
        if not snapshot:
            telegram_send("⚠️ No snapshot available — restoration is not possible")
            return
        restored_on = 0
        left_off = 0
        for eid, previous_state in snapshot.items():
            if not eid.startswith("switch.") or "child_lock" in eid:
                continue
            try:
                if previous_state == "on":
                    ha_post("services/switch/turn_on", {"entity_id": eid})
                    restored_on += 1
                else:
                    left_off += 1
            except Exception:
                pass
        telegram_send(
            f"✅ Power outage recovery complete\n"
            f"🟢 {restored_on} plug(s) set back to ON\n"
            f"⚫ {left_off} plug(s) left OFF (normal state)"
        )
        log.info(f"✅ Power outage recovered: {restored_on} ON, {left_off} OFF")
        return

    if data.startswith("outage_leave:"):
        telegram_send("✅ OK — plugs left as-is.")
        return

    # Appliance run suggestions.
    if data.startswith("suggestion_now:"):
        entity_id = data.split(":", 1)[1]
        telegram_send(f"✅ Got it! Start the appliance when ready.\nI'm monitoring the cycle.")
        return

    if data.startswith("suggestion_no:"):
        telegram_send("✅ OK, no machine today.")
        return

    if data.startswith("suggestion_1h:"):
        telegram_send("⏰ I'll remind your in 1 hour.")
        # Store reminder
        mem_set("reminder_machine", (datetime.now() + timedelta(hours=1)).isoformat())
        return


def ha_get_areas_mapping():
    """Retrieves mapping area_id -> name readable via /api/template (only reliable endpoint on HA Green)."""
    try:
        url = f"{CFG['ha_url']}/api/template"
        headers = {"Authorization": f"Bearer {CFG['ha_token']}", "Content-Type": "application/json"}
        template = "{% for area in areas() %}AREA:::{{ area_name(area) }}:::{{ area }};;;{% endfor %}"
        r = requests.post(url, headers=headers, json={"template": template}, verify=False, timeout=10)
        if r.status_code == 200:
            mapping = {}
            for chunk in r.text.split(";;;"):
                chunk = chunk.strip()
                if chunk.startswith("AREA:::"):
                    parts = chunk.split(":::")
                    if len(parts) == 3:
                        name = parts[1].strip()
                        aid = parts[2].strip()
                        if aid:
                            mapping[aid] = name
            if mapping:
                log.info(f"\u2705 HA Areas: {len(mapping)} rooms via /api/template")
                return mapping
    except Exception as ex:
        log.debug(f"Areas template: {ex}")

    # Fallback REST (HA older versions)
    for endpoint in ["/api/config/area_registry/list", "/api/areas"]:
        try:
            url = f"{CFG['ha_url']}{endpoint}"
            headers = {"Authorization": f"Bearer {CFG['ha_token']}"}
            r = requests.get(url, headers=headers, verify=False, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and data:
                    mapping = {}
                    for a in data:
                        aid = a.get("area_id") or a.get("id", "")
                        name = a.get("name", "") or aid
                        if aid:
                            mapping[aid] = name
                    if mapping:
                        log.info(f"✅ HA Areas: {len(mapping)} rooms via {endpoint}")
                        return mapping
        except Exception as ex:
            log.debug(f"Areas {endpoint} : {ex}")

    log.warning("⚠️ HA areas unavailable")
    return {}


def ha_get_entity_areas():
    """Retrieves mapping entity_id -> area_id via /api/template (only reliable endpoint on HA Green)."""
    entity_map = {}

    try:
        url = f"{CFG['ha_url']}/api/template"
        headers = {"Authorization": f"Bearer {CFG['ha_token']}", "Content-Type": "application/json"}
        template = '{% for area in areas() %}{% for eid in area_entities(area) %}{{ eid }}|{{ area }}\n{% endfor %}{% endfor %}'
        r = requests.post(url, headers=headers, json={"template": template}, verify=False, timeout=15)
        if r.status_code == 200:
            text = r.text.strip()
            log.debug(f"entity_areas template response: {len(text)} chars")
            for line in text.split("\n"):
                line = line.strip()
                if "|" in line:
                    eid, aid = line.split("|", 1)
                    entity_map[eid.strip()] = aid.strip()
            log.debug(f"entity_areas parsed: {len(entity_map)} entities")
            if entity_map:
                return entity_map
        else:
            log.warning(f"entity_areas template: HTTP {r.status_code}")
    except Exception as ex:
        log.warning(f"entity_areas template: {ex}")

    try:
        url = f"{CFG['ha_url']}/api/template"
        headers = {"Authorization": f"Bearer {CFG['ha_token']}", "Content-Type": "application/json"}
        template = '{% for area in areas() %}{% for dev in area_devices(area) %}{% for eid in device_entities(dev) %}{{ eid }}|{{ area }}\n{% endfor %}{% endfor %}{% endfor %}'
        r = requests.post(url, headers=headers, json={"template": template}, verify=False, timeout=15)
        if r.status_code == 200:
            for line in r.text.strip().split("\n"):
                if "|" in line:
                    eid, aid = line.split("|", 1)
                    eid = eid.strip()
                    if eid not in entity_map:
                        entity_map[eid.strip()] = aid.strip()
    except Exception as ex:
        log.debug(f"device_areas template: {ex}")

    # Fallback REST
    if not entity_map:
        for endpoint in ["/api/config/entity_registry/list", "/api/entity_registry"]:
            try:
                url = f"{CFG['ha_url']}{endpoint}"
                headers = {"Authorization": f"Bearer {CFG['ha_token']}"}
                r = requests.get(url, headers=headers, verify=False, timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list):
                        for e in data:
                            entity_map[e.get("entity_id", "")] = e.get("area_id") or ""
                        return entity_map
            except Exception as ex:
                log.debug(f"entity_areas {endpoint}: {ex}")

    return entity_map


def ha_get_assist_exposed_entities():
    """Return Assist-exposed entity_ids when available.

    Uses the Home Assistant WebSocket API command:
      `homeassistant/expose_entity/list`

    Returns:
      - a `set(entity_id)` when exposure metadata is available
      - `None` when exposure metadata cannot be determined
    """
    def _ws_url():
        base = str(CFG.get("ha_url", "") or "").strip().rstrip("/")
        if not base:
            return ""
        if base.endswith("/api/websocket"):
            return base
        if base.startswith("https://"):
            return "wss://" + base[len("https://"):] + "/api/websocket"
        if base.startswith("http://"):
            return "ws://" + base[len("http://"):] + "/api/websocket"
        if base.startswith("wss://") or base.startswith("ws://"):
            return base + "/api/websocket"
        return "ws://" + base + "/api/websocket"

    ws_url = _ws_url()
    if ws_url:
        try:
            import ssl
            import websocket

            sslopt = None
            if ws_url.startswith("wss://"):
                sslopt = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}

            ws = websocket.create_connection(ws_url, timeout=12, sslopt=sslopt)
            try:
                hello = json.loads(ws.recv())
                if hello.get("type") != "auth_required":
                    log.debug(f"Assist exposure WS unexpected hello: {hello.get('type')}")

                ws.send(json.dumps({
                    "type": "auth",
                    "access_token": str(CFG.get("ha_token", "") or ""),
                }))
                auth_reply = json.loads(ws.recv())
                if auth_reply.get("type") != "auth_ok":
                    log.warning(f"Assist exposure WS auth failed: {auth_reply.get('type')}")
                    return None

                req_id = int(time.time() * 1000) % 1000000000
                ws.send(json.dumps({"id": req_id, "type": "homeassistant/expose_entity/list"}))

                deadline = time.time() + 10
                while time.time() < deadline:
                    msg = json.loads(ws.recv())
                    if msg.get("id") != req_id:
                        continue
                    if msg.get("type") != "result" or not msg.get("success"):
                        log.warning("Assist exposure WS returned non-success result")
                        return None

                    result = msg.get("result", {}) or {}
                    exposed_entities = result.get("exposed_entities", {}) or {}
                    if not isinstance(exposed_entities, dict):
                        return None

                    exposed = set()
                    flags_seen = 0
                    for entity_id, assistants in exposed_entities.items():
                        if not isinstance(assistants, dict):
                            continue
                        if "conversation" not in assistants:
                            continue
                        flags_seen += 1
                        if assistants.get("conversation") is True:
                            exposed.add(entity_id)

                    if flags_seen > 0:
                        log.info(f"🗣️ Assist exposure WS map loaded: {len(exposed)} entity/entities exposed to conversation")
                        return exposed

                    # If there are no explicit conversation flags, HA will use
                    # defaults. In that case, don't force a restrictive filter.
                    log.info("🗣️ Assist exposure WS returned no explicit conversation flags; keeping discovery open")
                    return None
            finally:
                try:
                    ws.close()
                except Exception:
                    pass
        except Exception as ex:
            log.warning(f"Assist exposure WS unavailable: {ex}")

    # Fallback to registry options when WS is unavailable.
    endpoints = ["/api/config/entity_registry/list", "/api/entity_registry"]
    headers = {"Authorization": f"Bearer {CFG['ha_token']}"}
    for endpoint in endpoints:
        try:
            url = f"{CFG['ha_url']}{endpoint}"
            r = requests.get(url, headers=headers, verify=False, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json()
            if not isinstance(data, list):
                continue

            exposed = set()
            flags_seen = 0
            for row in data:
                if not isinstance(row, dict):
                    continue
                entity_id = str(row.get("entity_id", "") or "").strip()
                if not entity_id:
                    continue
                options = row.get("options", {})
                if not isinstance(options, dict):
                    continue
                convo = options.get("conversation", {})
                if not isinstance(convo, dict):
                    continue
                if "should_expose" not in convo:
                    continue
                flags_seen += 1
                if convo.get("should_expose") is True:
                    exposed.add(entity_id)

            if flags_seen > 0:
                log.info(f"🗣️ Assist exposure registry map loaded: {len(exposed)} entity/entities explicitly exposed")
                return exposed
        except Exception as ex:
            log.debug(f"Assist exposure registry ({endpoint}): {ex}")

    return None


def ha_refresh_areas():
    """Load HA areas and update rooms in entity_map."""
    # # global _areas_id_to_name, _entity_areas    # via shared# via shared
    shared._areas_id_to_name = ha_get_areas_mapping()
    shared._entity_areas     = ha_get_entity_areas()
    log.info(f"✅ HA Areas: {len(shared._areas_id_to_name)} rooms, {len(shared._entity_areas)} entities")

    _KNOWN_ROOMS_REFRESH = [
        "kitchen", "living_room", "guest bedroom", "child bedroom", "bedroom",
        "laundry_room", "garage", "office", "salle of bain", "sdb",
        "entry", "hallway", "garden", "terrace", "attic", "basement",
    ]
    try:
        conn = sqlite3.connect(DB_PATH)
        updated_count = 0
        rows = conn.execute("SELECT entity_id, room, friendly_name FROM entity_map WHERE room IS NULL OR room=''").fetchall()
        for eid, current_room, fname in rows:
            new_room = ""
            # Attempt 1: Home Assistant area registry.
            area_id = shared._entity_areas.get(eid, "")
            if area_id:
                new_room = shared._areas_id_to_name.get(area_id, area_id)
            # Attempt 2: infer from friendly name.
            if not new_room and fname:
                fn = fname.lower()
                for p in _KNOWN_ROOMS_REFRESH:
                    if p in fn:
                        new_room = p
                        break
            if new_room:
                conn.execute("UPDATE entity_map SET room=? WHERE entity_id=?", (new_room, eid))
                updated_count += 1
        if updated_count > 0:
            conn.commit()
            log.info(f"🏠 {updated_count} rooms updated in mapping")
        conn.close()
    except Exception as ex:
        log.error(f"Room update: {ex}")


def ha_get_area(entity_id):
    """Returns the readable room name for an entity"""
    area_id = shared._entity_areas.get(entity_id, "")
    if area_id:
        return shared._areas_id_to_name.get(area_id, area_id)
    return ""


def _monitored_heat_pump_correlee(index, states):
    """Heat pump: alert only on a real failure, not a thermostat cycle.
    Stay silent if no heat pump is configured.
    The thermostat naturally cycles on and off. Alert only if:
    1. Heat pump is OFF, not auto/heat, which usually means manually disabled.
    2. Outdoor temperature < 3°C.
    3. Indoor temperature < 17°C and falling.
    This means the heat pump is off and the home is cooling."""
    if not role_get("heat_pump_climate"):
        return
    heat_pump_entity = None
    heat_pump_state  = None
    for e in states:
        eid = e["entity_id"]
        if not eid.startswith("climate."):
            continue
        carto = entity_map_get(eid)
        if carto and "heating" in carto[0].lower():
            heat_pump_entity = eid
            heat_pump_state  = e["state"]
            break

    if heat_pump_entity is None:
        return

    if heat_pump_state in ["auto", "heat", "cool", "fan_only", "heat_cool"]:
        return

    temp_ext = None
    try:
        e_ext = index.get(role_get("outdoor_temperature") or "sensor.ecojoko_outdoor_temperature")
        if e_ext and e_ext["state"] not in ["unavailable", "unknown"]:
            temp_ext = float(e_ext["state"])
    except Exception:
        pass

    if temp_ext is None or temp_ext > 3:
        return  # Not cold enough to be critical

    temp_int = None
    try:
        e_int = index.get(role_get("indoor_temperature") or "sensor.ecojoko_indoor_temperature")
        if e_int and e_int["state"] not in ["unavailable", "unknown"]:
            temp_int = float(e_int["state"])
    except Exception:
        pass

    if temp_int is None or temp_int >= 17:
        return  # Indoor temperature is still warm — not urgent

    try:
        prev_json = mem_get("previous_snapshot")
        if prev_json:
            prev = json.loads(prev_json)
            prev_int = prev.get("temp_int")
            if prev_int is not None and temp_int >= prev_int:
                return  # Temperature stable or rising — no problem
    except Exception:
        pass

    # This is a real emergency : heat pump off + frost + home froide + falling temperature
    _alert_if_new(
        "heat_pump_off_froid",
        f"🚨 heat pump OFF - home cooling\n"
        f"Ext: {temp_ext:.1f}°C | Int: {temp_int:.1f}°C (falling)\n"
        f"heat pump {heat_pump_entity} : {heat_pump_state}\n"
        f"Check: thermostat / circuit breaker / mode",
        delay_h=6
    )


def ha_get_context_intelligent(question, states=None):
    if states is None:
        states = ha_get("states")
    if not states:
        return "HA unreachable"

    index = {e["entity_id"]: e for e in states}
    categories_available = entity_map_get_all_categories()
    target_categories = []
    if categories_available:
        prompt_detection = (
            f"Question : \"{question}\"\n"
            f"Available categories: {', '.join(categories_available)}\n"
            "List ONLY the relevant categories, comma-separated."
        )

        try:
            blocks, t_in, t_out = llm_provider.llm_completion(
                CFG, [{"role": "user", "content": prompt_detection}],
                max_tokens=80
            )
            log_token_usage(t_in, t_out)
            response_text = llm_provider.stream_text(blocks)
            target_categories = [c.strip() for c in response_text.strip().split(",") if c.strip()]
        except Exception as e:
            log.error(f"❌ Category detection: {e}")
            target_categories = []

    lines = []
    for cat in target_categories:
        entities_cat = entity_map_get_by_category(cat)
        for entity_id, subcategory, room in entities_cat:
            if entity_id in index:
                e = index[entity_id]
                unit = e.get("attributes", {}).get("unit_of_measurement", "")
                display = _ha_entity_display(e, room)
                lines.append(f"{display} = {e['state']} {unit} [entity_id={entity_id}]".strip())

    q_low = str(question or "").lower()

    # Domain-focused scope expansion (automations, scenes, entities, etc.)
    domain_keywords = {
        "automation": ["automation", "automations", "routine", "routines"],
        "scene": ["scene", "scenes"],
        "script": ["script", "scripts"],
        "light": ["light", "lights", "lamp", "lamps"],
        "switch": ["switch", "switches", "outlet", "outlets"],
        "cover": ["cover", "covers", "blind", "blinds", "shade", "shades", "gate", "garage"],
        "lock": ["lock", "locks", "door lock", "deadbolt"],
        "climate": ["climate", "thermostat", "thermostats", "hvac", "heat pump"],
        "fan": ["fan", "fans"],
        "media_player": ["media", "speaker", "tv", "chromecast"],
        "binary_sensor": ["binary sensor", "contact", "motion", "door", "window", "occupancy"],
        "sensor": ["sensor", "sensors", "energy", "power", "consumption", "temperature", "humidity"],
    }
    hinted_domains = []
    for domain, keys in domain_keywords.items():
        if any(k in q_low for k in keys):
            hinted_domains.append(domain)

    for domain in hinted_domains:
        added = 0
        for e in states:
            eid = e.get("entity_id", "")
            if not eid.startswith(domain + "."):
                continue
            unit = e.get("attributes", {}).get("unit_of_measurement", "")
            room = ha_get_area(eid)
            display = _ha_entity_display(e, room)
            lines.append(f"{display} = {e.get('state', '?')} {unit} [entity_id={eid}]".strip())
            added += 1
            if added >= 30:
                break

    # Query token match against full HA state to improve scope coverage.
    stop_words = {
        "the", "a", "an", "please", "can", "you", "would", "will", "turn", "switch", "toggle",
        "set", "to", "at", "on", "off", "entity", "entities", "device", "devices", "status", "state",
        "room", "area", "in", "of", "for", "and", "is", "are", "what", "which", "show", "list",
        "all", "current", "currently", "today",
    }
    q_key = _ha_text_key(question or "")
    q_tokens = [tok for tok in q_key.split() if tok and tok not in stop_words]
    if q_tokens:
        scored = []
        for e in states:
            eid = e.get("entity_id", "")
            attrs = e.get("attributes", {})
            room = ha_get_area(eid)
            hay = _ha_text_key(f"{eid} {attrs.get('friendly_name', '')} {room}")
            score = 0
            if q_key and q_key in hay:
                score += 70
            if all(tok in hay for tok in q_tokens):
                score += 80
            score += sum(8 for tok in q_tokens if tok in hay)
            if score > 0:
                scored.append((score, e, room))
        scored.sort(key=lambda item: item[0], reverse=True)
        for score, e, room in scored[:60]:
            eid = e.get("entity_id", "")
            unit = e.get("attributes", {}).get("unit_of_measurement", "")
            display = _ha_entity_display(e, room)
            lines.append(f"{display} = {e.get('state', '?')} {unit} [entity_id={eid}]".strip())
    include_calendars = any(
        key in q_low
        for key in (
            "calendar", "event", "events", "schedule", "scheduled",
            "meeting", "appointment", "trash", "garbage", "recycling",
            "pickup", "collection", "bin day",
        )
    )

    if include_calendars:
        try:
            now_dt = datetime.now()
            start_dt = now_dt.strftime("%Y-%m-%dT00:00:00")
            end_dt = (now_dt + timedelta(hours=72)).strftime("%Y-%m-%dT23:59:59")
            headers_cal = {"Authorization": f"Bearer {CFG['ha_token']}"}
            url_cals = f"{CFG['ha_url']}/api/calendars"

            r_list = requests.get(url_cals, headers=headers_cal, verify=False, timeout=15)
            log.debug(f"Calendars API: {r_list.status_code} | {len(r_list.json()) if r_list.status_code == 200 else r_list.text[:100]}")

            if r_list.status_code == 200:
                for cal_info in r_list.json():
                    eid = cal_info.get("entity_id", "")
                    fname = cal_info.get("name", eid)
                    url_ev = f"{CFG['ha_url']}/api/calendars/{eid}?start={start_dt}&end={end_dt}"
                    try:
                        r_ev = requests.get(url_ev, headers=headers_cal, verify=False, timeout=15)
                        if r_ev.status_code == 200:
                            events = r_ev.json()
                            for ev in events[:5]:
                                summary = ev.get("summary", "?")
                                ev_start = ev.get("start", {})
                                date_str = ev_start.get("dateTime", ev_start.get("date", "?"))
                                try:
                                    if "T" in str(date_str):
                                        dt_ev = datetime.fromisoformat(date_str.replace("Z", "+00:00")[:19])
                                        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                                        readable_date = f"{days[dt_ev.weekday()]} {dt_ev.day}/{dt_ev.month} at {dt_ev.hour}:{dt_ev.minute:02d}"
                                    else:
                                        readable_date = str(date_str)
                                except Exception:
                                    readable_date = str(date_str)
                                lines.append(f"📅 CALENDAR {fname}: {summary} — {readable_date}")
                            if not events:
                                lines.append(f"📅 CALENDAR {fname}: nothing in the next 72h")
                    except Exception as ex_ev:
                        log.debug(f"Calendar events {eid}: {ex_ev}")
        except Exception as ex_cal:
            log.debug(f"Calendars API error: {ex_cal}")

    # ═══ BUILD CONTEXT ═══
    # Calendars FIRST (priority for daily questions)
    calendar_lines = [l for l in lines if l.startswith("📅")]
    other_lines = [l for l in lines if not l.startswith("📅")]
    ordered_lines = calendar_lines + other_lines

    # Deduplicate while preserving order.
    deduped = []
    seen = set()
    for line in ordered_lines:
        key = line.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(line)

    context = "Available data:\n" + "\n".join(deduped[:180]) if deduped else _ha_summary_generique(states)

    memory_store_extra = []

    # Baselines: compare current values to habits.
    now = datetime.now()
    day = now.weekday()
    hour = now.hour
    try:
        conn = sqlite3.connect(DB_PATH)
        for eid in list(BASELINE_ENTITIES.keys()):
            row = conn.execute(
                "SELECT avg_value, sample_count FROM baselines WHERE entity_id=? AND weekday=? AND hour=?",
                (eid, day, hour)
            ).fetchone()
            if row and row[1] >= 5:
                e = index.get(eid)
                if e and e["state"] not in ("unavailable", "unknown"):
                    try:
                        val = float(e["state"])
                        avg = row[0]
                        ecart = abs(val - avg) / avg * 100 if avg > 0 else 0
                        label = BASELINE_ENTITIES[eid]
                        memory_store_extra.append(
                            f"BASELINE {label}: current={val:.0f}, habituel={avg:.0f} "
                            f"(deviation {ecart:.0f}%, {row[1]} measurements)"
                        )
                    except Exception:
                        pass
        conn.close()
    except Exception:
        pass

    # Recent appliance cycles
    try:
        conn = sqlite3.connect(DB_PATH)
        cycles = conn.execute(
            "SELECT friendly_name, started_at, duration_min, consumption_kwh FROM appliance_cycles "
            "WHERE ended_at IS NOT NULL ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
        conn.close()
        if cycles:
            memory_store_extra.append("RECENT APPLIANCE CYCLES:")
            for fname, started_at, duration, consumption in cycles:
                date = started_at[:10] if started_at else "?"
                memory_store_extra.append(f"  {fname} — {date} | {duration}min | {consumption:.2f}kWh")
    except Exception:
        pass

    try:
        useful_key_names = ["last_summary", "ha_scan_date", "ha_entities_count", "discovery_count"]
        for key_name in useful_key_names:
            val = mem_get(key_name)
            if val:
                memory_store_extra.append(f"MEM {key_name} = {val}")
    except Exception:
        pass

    try:
        setup_data, _ = skill_get("conversational_setup")
        notes = setup_data.get("notes", []) if isinstance(setup_data, dict) else []
        if notes:
            memory_store_extra.append("USER-PROVIDED HOME SETUP NOTES:")
            for note in notes[-10:]:
                if isinstance(note, dict) and note.get("text"):
                    memory_store_extra.append(f"  - {note['text']}")
    except Exception:
        pass

    try:
        rate = rate_get()
        price_now = rate_current_kwh_price()
        ttype = rate.get("type", "base")
        provider = rate.get("provider", "?")
        is_weekend_day = _is_weekend_or_holiday()
        info_rate = f"RATE: {provider} {ttype} | Current price: {price_now}/kWh"
        if ttype in ("hphc", "weekend_hphc", "weekend_plus_hphc"):
            is_off_peak = _is_off_peak_hour_ranges(rate.get("off_peak_hours", []))
            info_rate += f" ({'off-peak' if is_off_peak else 'peak'})"
        if is_weekend_day:
            info_rate += " (weekend/holiday)"
        chosen_day = rate.get("chosen_day")
        if chosen_day is not None:
            days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
            info_rate += f" | Selected day: {days[chosen_day]}"
        memory_store_extra.append(info_rate)
        # Summary rate of the month
        data_rate, nb_rate = skill_get("optimisation_rate")
        if data_rate and data_rate.get("total_kwh", 0) > 1:
            periods = data_rate.get("periods", {})
            summary = " | ".join(f"{p}:{v['kwh']:.0f}kWh/{v['eur']:.1f}" for p, v in periods.items())
            memory_store_extra.append(f"MONTHLY RATE SUMMARY: {data_rate['total_kwh']:.0f}kWh {data_rate['total_eur']:.1f} | {summary}")
    except Exception:
        pass

    try:
        conn_exp = sqlite3.connect(DB_PATH)
        expertise = conn_exp.execute(
            "SELECT category, insight, confidence FROM expertise "
            "WHERE confidence >= 0.4 ORDER BY confidence DESC LIMIT 10"
        ).fetchall()
        conn_exp.close()
        if expertise:
            memory_store_extra.append("ACQUIRED EXPERTISE (rules learned by AI):")
            for cat, insight, conf in expertise:
                stars = "★" * min(5, int(conf * 5))
                memory_store_extra.append(f"  [{cat}] {stars} {insight}")
    except Exception:
        pass

    try:
        conn_hyp = sqlite3.connect(DB_PATH)
        hyps = conn_hyp.execute(
            "SELECT statement, confidence, confirmations, predictions FROM hypotheses "
            "WHERE active=1 AND confidence >= 0.6 AND predictions >= 3 ORDER BY confidence DESC LIMIT 5"
        ).fetchall()
        if hyps:
            memory_store_extra.append("VALIDATED HYPOTHESES (reliable predictions):")
            for statement, conf, confirm, pred in hyps:
                memory_store_extra.append(f"  [{conf:.0%}] {statement} ({confirm}/{pred} confirmed)")

        # Intelligence score
        score_row = conn_hyp.execute(
            "SELECT score_global, details FROM intelligence_score ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if score_row:
            details = json.loads(score_row[1]) if score_row[1] else {}
            memory_store_extra.append(f"SCORE INTELLIGENCE: {score_row[0]}/100 ({details.get('level', '?')})")
        conn_hyp.close()
    except Exception:
        pass

    try:
        data_host, nb_host = skill_get("health_host")
        if data_host and data_host.get("history"):
            last = data_host["history"][-1].get("metrics", {})
            ram = last.get("ram_mb", "?")
            free_disk = last.get("disque_libre_mb", "?")
            ha_latency = last.get("latence_ha_ms", "?")
            memory_store_extra.append(f"HOST: RAM={ram}MB | Free disk={free_disk}MB | HA latency={ha_latency}ms")
    except Exception:
        pass

    try:
        data_host, nb_host = skill_get("host")
        if data_host and "last_mesure" in data_host:
            m = data_host["last_mesure"]
            ram = m.get("ram_pct", 0)
            disk = m.get("disque_pct", 0)
            if ram > 70 or disk > 80:
                memory_store_extra.append(
                    f"HOST: RAM {ram:.0f}% | Disk {disk:.0f}% | "
                    f"DB {m.get('db_kb', '?')}KB | Load {m.get('cpu_load5', '?')}"
                )
    except Exception:
        pass

    try:
        last_analysis = mem_get("last_analysis_ia")
        last_date = mem_get("last_analysis_ia_date")
        if last_analysis and last_date:
            memory_store_extra.append(f"LAST ANALYSIS ({last_date[:16]}) : {last_analysis[:300]}")
    except Exception:
        pass

    try:
        eco_month = get_savings_month()
        if eco_month["nb_actions"] > 0:
            memory_store_extra.append(
                f"CURRENT MONTH SAVINGS: {eco_month['total_eur']:.2f} | "
                f"{eco_month['total_kwh']:.1f} kWh | {eco_month['nb_actions']} actions"
            )
            for t, d in eco_month["by_type"].items():
                memory_store_extra.append(f"  {t}: {d['eur']:.2f} ({d['nb']} actions)")
        previous_month = (datetime.now().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        previous_savings = get_savings_month(previous_month)
        if previous_savings["nb_actions"] > 0:
            memory_store_extra.append(
                f"PREVIOUS MONTH SAVINGS ({previous_month}): {previous_savings['total_eur']:.2f} | "
                f"{previous_savings['total_kwh']:.1f} kWh | {previous_savings['nb_actions']} actions"
            )
    except Exception:
        pass

    # Grid bill projection at the end of the month
    try:
        import calendar as _cal
        data_rate, nb_rate = skill_get("optimisation_rate")
        if data_rate and data_rate.get("total_kwh", 0) > 1:
            now_proj = datetime.now()
            current_day = now_proj.day
            days_month = _cal.monthrange(now_proj.year, now_proj.month)[1]
            days_remaining = days_month - current_day

            consumption_kwh = data_rate["total_kwh"]
            consumption_eur = data_rate["total_eur"]

            if current_day > 0:
                kwh_by_day = consumption_kwh / current_day
                eur_by_day = consumption_eur / current_day
                proj_kwh = kwh_by_day * days_month
                proj_eur = eur_by_day * days_month

                # Grid subscription cost, adjustable by configuration.
                subscription_monthly = CFG.get("grid_subscription_monthly", 16.0)
                proj_total = proj_eur + subscription_monthly

                memory_store_extra.append(
                    f"GRID BILL PROJECTION: "
                    f"Consumption day {current_day} = {consumption_kwh:.0f} kWh / {consumption_eur:.1f} | "
                    f"Projected month = {proj_kwh:.0f} kWh / {proj_eur:.1f} consumption + {subscription_monthly:.0f} subscription = ~{proj_total:.0f} total | "
                    f"Average {kwh_by_day:.1f} kWh/day / {eur_by_day:.2f}/day"
                )

                periods = data_rate.get("periods", {})
                period_names = {"hp": "Peak", "hc": "Off-peak", "base": "Base", "weekday": "Weekday", "weekend_day": "Weekend/selected day"}
                for p, vals in periods.items():
                    pct = vals["kwh"] / consumption_kwh * 100 if consumption_kwh > 0 else 0
                    memory_store_extra.append(f"  {period_names.get(p, p)}: {vals['kwh']:.0f} kWh ({pct:.0f}%) / {vals['eur']:.1f}")

                # Solar
                solar_kwh = data_rate.get("solar_kwh", 0)
                if solar_kwh > 0:
                    eco_sol = solar_kwh * (consumption_eur / consumption_kwh if consumption_kwh > 0 else 0.20)
                    memory_store_extra.append(f"  Solar self-consumed: {solar_kwh:.0f} kWh → ~{eco_sol:.1f} saved (not billed)")
    except Exception:
        pass

    # Skills learned
    try:
        data_sol, nb_sol = skill_get("window_solar")
        if data_sol and nb_sol >= 10:
            day_str = str(datetime.now().weekday())
            if day_str in data_sol:
                best = max(data_sol[day_str].items(), key=lambda x: x[1][0])
                memory_store_extra.append(f"SKILL window_solar: pic {best[0]}h → {int(best[1][0])} W ({nb_sol} learning samples)")

        data_cyc, nb_cyc = skill_get("cycle_signatures")
        if data_cyc:
            for eid, info in list(data_cyc.items())[:3]:
                memory_store_extra.append(
                    f"SKILL machine {info['name']}: ~{info['duration_avg']:.0f}min, "
                    f"~{info['consumption_avg']:.2f}kWh, {info['nb_cycles']} cycles"
                )

        heat_pump_data, heat_pump_count = skill_get("heat_pump_behavior")
        if heat_pump_data and heat_pump_count >= 10:
            memory_store_extra.append(f"SKILL heat_pump_behavior: {heat_pump_count} observations")
    except Exception:
        pass

    if memory_store_extra:
        context += "\n\n=== MEMORY / HISTORY ===\n" + "\n".join(memory_store_extra)

    return context


def _ha_summary_generique(states):
    if not states:
        return "HA unreachable"

    buckets = {}
    for e in states:
        eid = e.get("entity_id", "")
        if "." not in eid:
            continue
        domain = eid.split(".", 1)[0]
        buckets.setdefault(domain, []).append(e)

    priority_domains = [
        "light", "switch", "climate", "cover", "lock", "fan", "media_player",
        "vacuum", "scene", "script", "automation", "binary_sensor", "sensor",
    ]
    ordered_domains = [d for d in priority_domains if d in buckets]
    ordered_domains.extend(sorted(d for d in buckets if d not in ordered_domains))

    lines = []
    for domain in ordered_domains:
        items = buckets.get(domain, [])
        if not items:
            continue
        lines.append(f"[{domain}] count={len(items)}")

        shown = 0
        for entity in items:
            if shown >= 10:
                break
            eid = entity.get("entity_id", "")
            state = entity.get("state", "?")
            attrs = entity.get("attributes", {})
            unit = attrs.get("unit_of_measurement", "")
            room = ha_get_area(eid)
            room_txt = f" [{room}]" if room else ""
            lines.append(f"  {eid}{room_txt}={state}{(' ' + str(unit)) if unit else ''}")
            shown += 1

        remaining = len(items) - shown
        if remaining > 0:
            lines.append(f"  ... +{remaining} more")

    return "\n".join(lines[:220])


def _match_pattern(entity_id, fname):
    """Tente of categoriser a entity via the patterns."""
    import re
    eid_low = entity_id.lower()
    fname_low = (fname or "").lower()
    combined = eid_low + " " + fname_low
    for p_id, p_name, cat, subcategory, desc in PATTERNS_AUTO:
        if p_id in combined:
            if not p_name or re.search(p_name, combined):
                return cat, subcategory, desc
    return None, None, None


def _build_intelligent_question(entity_id, fname, state, attrs):
    """Use the configured AI provider to build a precise question."""
    unit = attrs.get("unit_of_measurement", "")
    device_class = attrs.get("device_class", "")
    prompt = (
        f"You are the user's home assistant.\n"
        f"entity_id: {entity_id}\n"
        f"friendly_name: {fname}\n"
        f"state: {state} {unit}\n"
        f"device_class: {device_class}\n\n"
        f"Propose ONE sentence: what this entity does and its category.\n"
        f"Categories: energy_battery, energy_production, energy_forecast, weather, connected_plug, heating, ignore\n\n"
        f"Respond ONLY in JSON: {{\"category\": \"...\", \"description\": \"...\"}}"
    )
    try:
        blocks, t_in, t_out = llm_provider.llm_completion(
            CFG, [{"role": "user", "content": prompt}],
            max_tokens=150
        )
        import json as _json
        txt = llm_provider.stream_text(blocks).strip()
        txt = txt.replace("```json", "").replace("```", "").strip()
        data = _json.loads(txt)
        log_token_usage(t_in, t_out)
        return data.get("category", "unknown"), data.get("description", fname)
    except Exception as ex:
        log.warning(f"⚠️ Question intelligente {entity_id}: {ex}")
        return "unknown", fname


def ask_entity_question(entity_id, fname, category, description):
    """Send a question Telegram with buttons Yes/No"""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT question_asked, response FROM pending_entities WHERE entity_id=?",
        (entity_id,)
    ).fetchone()
    conn.close()

    if row and row[0] == 1 and not row[1]:
        log.debug(f"Question already asked without answer: {entity_id}")
        return

    if row and row[1]:
        log.debug(f"Entity already answered: {entity_id} → {row[1]}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT OR REPLACE INTO pending_entities
           (entity_id, friendly_name, proposed_category, description, question_asked, created_at)
           VALUES (?, ?, ?, ?, 1, ?)""",
        (entity_id, fname, category, description, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

    msg = (
        f"🔍 New entity discovered\n"
        f"**{fname}**\n"
        f"Suggested category: {category}\n"
        f"Assumed role: {description}\n\n"
        f"Is this correct ?"
    )
    telegram_send_buttons(msg, [
        {"text": "✅ Yes",   "callback_data": f"entity_ori:{entity_id}"},
        {"text": "❌ No",   "callback_data": f"entity_no:{entity_id}"},
        {"text": "↩️ Cancel","callback_data": f"entity_cancel:{entity_id}"},
    ])
    log.info(f"❓ Question asked: {fname} ({entity_id})")


def _check_entity_map_consistency(index):
    """Checks that the entities already in memory_store are still coherent"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT entity_id, category, friendly_name FROM entity_map"
    ).fetchall()
    conn.close()

    for entity_id, category, fname in rows:
        if "plug" in entity_id and category in ("energy_battery", "energy_production", "energy_forecast"):
            log.warning(f"🔧 Correction: {entity_id} misclassified")
            conn2 = sqlite3.connect(DB_PATH)
            conn2.execute(
                "UPDATE entity_map SET category='ignore' WHERE entity_id=?",
                (entity_id,)
            )
            conn2.commit()
            conn2.close()
            continue

        if entity_id not in index:
            if category in ("ignore", "confirmed_missing"):
                continue

            alert_key = f"missing_entity_{entity_id}"
            already_alerted = mem_get(alert_key)
            if already_alerted:
                continue

            mem_set(alert_key, datetime.now().isoformat())
            room_str = f" [{fname}]" if fname else ""
            telegram_send_buttons(
                f"⚠️ Entity gone from HA\n{entity_id}{room_str}\nCategory: {category}",
                [
                    {"text": "✅ Removed (normal)", "callback_data": f"missing_entity_ok:{entity_id}"},
                    {"text": "❌ Abnormal", "callback_data": f"missing_entity_ko:{entity_id}"},
                ]
            )
            log.warning(f"⚠️ Entity gone: {entity_id}")


def handle_pending_entities(index):
    """Silent infiltration scan for new entities.

    Auto-categorizes entities without per-entity Telegram prompts.
    When Assist exposure metadata is available, only entities exposed to
    Assist are processed.
    """
    conn = sqlite3.connect(DB_PATH)
    known = set(
        r[0] for r in conn.execute(
            "SELECT entity_id FROM entity_map WHERE category != 'ignore'"
        ).fetchall()
    )
    pending = set(r[0] for r in conn.execute(
        "SELECT entity_id FROM pending_entities WHERE response IS NULL OR response = ''"
    ).fetchall())
    if pending:
        conn.execute(
            "UPDATE pending_entities SET response='auto' WHERE response IS NULL OR response = ''"
        )
        conn.commit()
    conn.close()

    domains_ignores = {
        "persistent_notification", "group", "zone", "sun",
        "input_boolean", "input_number", "input_select",
        "input_text", "input_datetime", "timer", "counter",
        "script", "scene", "tag", "device_tracker",
        "automation", "button", "select", "update", "number"
    }

    assist_exposed = ha_get_assist_exposed_entities()
    assist_filter_active = assist_exposed is not None

    auto_count = 0
    assist_skipped = 0
    conn = sqlite3.connect(DB_PATH)

    for entity_id, e in index.items():
        if entity_id in known or entity_id in pending:
            continue
        if assist_filter_active and entity_id not in assist_exposed:
            assist_skipped += 1
            continue
        domain = entity_id.split(".")[0]
        if domain in domains_ignores:
            continue
        if "plug" in entity_id:
            continue

        attrs = e.get("attributes", {})
        fname = attrs.get("friendly_name", entity_id)
        state  = e.get("state", "")

        cat, subcategory, desc = _match_pattern(entity_id, fname)

        if not cat:
            ai_category, _ai_description = _build_intelligent_question(entity_id, fname, state, attrs)
            cat = ai_category
            subcategory = ""

        if cat not in VALID_CATEGORIES:
            cat = "ignore"
        room = ha_get_area(entity_id)

        conn.execute(
            """INSERT OR REPLACE INTO entity_map
               (entity_id, category, subcategory, room, friendly_name, learned_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (entity_id, cat, subcategory or "", room, fname, datetime.now().isoformat())
        )
        auto_count += 1

    conn.commit()
    conn.close()

    if auto_count > 0:
        mode = "Assist-exposed entities only" if assist_filter_active else "all new entities"
        log.info(f"✅ Infiltration auto-categorized {auto_count} entity/entities ({mode})")
    elif assist_filter_active and assist_skipped > 0:
        log.info(f"🗣️ Infiltration skipped {assist_skipped} non-Assist entity/entities")

    _check_entity_map_consistency(index)


def _forcer_reclassification_anker(index):
    """Force the reclassification of the entities Anker"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT entity_id, category, friendly_name FROM entity_map WHERE category='ignore'"
    ).fetchall()
    conn.close()
    
    reclassifiees = 0
    
    for entity_id, category, fname in rows:
        if "solarbank_e1600" not in entity_id and "system_anker" not in entity_id:
            continue
        
        if entity_id not in index:
            continue
        
        attrs = index[entity_id].get("attributes", {})
        fname_ha = attrs.get("friendly_name", entity_id)
        
        cat, subcategory, desc = _match_pattern(entity_id, fname_ha)
        
        if not cat:
            if "solarbank_e1600" in entity_id:
                cat, subcategory, desc = "energy_battery", "other", "Anker Solarbank E1600"
            elif "system_anker" in entity_id:
                cat, subcategory, desc = "energy_production", "other", "Anker System"
            else:
                continue
        
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "UPDATE entity_map SET category=?, subcategory=?, friendly_name=? WHERE entity_id=?",
            (cat, subcategory, fname_ha, entity_id)
        )
        conn.commit()
        conn.close()
        
        log.info(f"🔴→⚡ Force Anker : {fname_ha} ({entity_id}) → {cat}")
        reclassifiees += 1
    
    if reclassifiees > 0:
        log.info(f"🔴→⚡ {reclassifiees} Anker entity/entities reclassified")


def _refresh_baseline_entities():
    """Refresh baseline entities from auto-discovered roles."""
    try:
        dynamic = role_baseline_entities()
        if dynamic:
            BASELINE_ENTITIES.update(dynamic)
            log.debug(f"Baseline entities refreshed: {len(dynamic)} role-based entries")
    except Exception as ex:
        log.debug(f"Baseline entity refresh skipped: {ex}")


def discover_automatically(states=None):
    ha_refresh_areas()
    _refresh_baseline_entities()
    log.info("🧠 Automatic discovery...")

    if states is None:
        states = ha_get("states")
        if not states:
            log.error("❌ Discovery: HA unreachable")
            return

    conn = sqlite3.connect(DB_PATH)
    known = set(r[0] for r in conn.execute('SELECT entity_id FROM entity_map').fetchall())
    conn.close()

    domains_ignores = {
        "persistent_notification", "group", "zone", "sun",
        "input_boolean", "input_number", "input_select",
        "input_text", "input_datetime", "timer", "counter",
        "script", "scene", "tag", "device_tracker"
    }

    new_items = [
        e for e in states
        if e["entity_id"] not in known
        and e["entity_id"].split(".")[0] not in domains_ignores
    ]

    if not new_items:
        log.info("✅ All entities already mapped")
        mem_set("discovery_date", datetime.now().isoformat())
        _refresh_known_entities()
        return

    auto_categorized = []
    items_for_ai  = []

    _KNOWN_ROOMS = [
        "kitchen", "living_room", "bedroom", "laundry_room", "garage", "office",
        "salle of bain", "sdb", "entree", "corloir", "jardin", "terrasse",
        "attic", "basement", "wc", "toilet", "guest bedroom", "child bedroom",
    ]

    def _extraire_room(fname):
        """Extracts the room from friendly_name when the HA API does not respond."""
        fn = fname.lower()
        for p in _KNOWN_ROOMS:
            if p in fn:
                return p
        return ""

    for e in new_items:
        eid    = e["entity_id"]
        attrs  = e.get("attributes", {})
        fname  = attrs.get("friendly_name", eid)
        room  = ha_get_area(eid) or _extraire_room(fname)
        domain = eid.split(".")[0]
        name_lower = eid.lower()

        if "plug" in name_lower:
            if domain == "sensor" and name_lower.endswith("_power"):
                auto_categorized.append((eid, "connected_plug", "power", room, fname))
            elif domain == "switch" and not name_lower.endswith("_child_lock"):
                auto_categorized.append((eid, "connected_plug", "command", room, fname))
            else:
                auto_categorized.append((eid, "ignore", "", room, fname))
        else:
            items_for_ai.append(e)

    if auto_categorized:
        conn = sqlite3.connect(DB_PATH)
        for eid, cat, subcategory, pc, fn in auto_categorized:
            conn.execute(
                '''INSERT OR REPLACE INTO entity_map
                   (entity_id, category, subcategory, room, friendly_name, learned_at)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (eid, cat, subcategory, pc, fn, datetime.now().isoformat())
            )
        nb_plugs = sum(1 for _, cat, _, _, _ in auto_categorized if cat == "connected_plug")
        conn.commit()
        conn.close()
        if nb_plugs > 0:
            log.info(f"🔌 {nb_plugs} new plug(s) categorized for monitoring")
        log.info(f"✅ Plugs: {nb_plugs} useful ones categorized")

    new_items = items_for_ai
    batch_size = 40
    total = 0

    for i in range(0, len(new_items), batch_size):
        batch = new_items[i:i + batch_size]
        list = []
        for e in batch:
            attrs = e.get("attributes", {})
            friendly = attrs.get("friendly_name", "")
            unit = attrs.get("unit_of_measurement", "")
            device_class = attrs.get("device_class", "")
            list.append(
                f"{e['entity_id']} | state:{e['state']} | unit:{unit} | "
                f"device_class:{device_class} | name:{friendly}"
            )

        prompt = (
            f"Categorize each entity into ONE of the categories:\n"
            f"{', '.join(VALID_CATEGORIES)}\n\n"
            f"Connected plugs with power measurement = 'connected_plug'\n"
            f"Respond ONLY in valid JSON:\n"
            f'[{{"entity_id":"...", "category":"...", "subcategory":"...", "room":""}}]\n\n'
            f"Entities:\n" + "\n".join(list)
        )

        try:
            blocks, t_in, t_out = llm_provider.llm_completion(
                CFG, [{"role": "user", "content": prompt}],
                max_tokens=2000
            )
            log_token_usage(t_in, t_out)
            text = llm_provider.stream_text(blocks).strip()
            match = re.search(r'\[.*\]', text, re.DOTALL)
            if not match:
                continue
            results = json.loads(match.group())
            conn = sqlite3.connect(DB_PATH)
            for item in results:
                eid = item.get("entity_id", "")
                cat = item.get("category", "ignore")
                subcategory = item.get("subcategory", "")
                e_orig = next((e for e in batch if e["entity_id"] == eid), None)
                fname = e_orig.get("attributes", {}).get("friendly_name", "") if e_orig else ""
                room_ha = ha_get_area(eid)
                room = room_ha if room_ha else item.get("room", "")
                conn.execute(
                    '''INSERT OR REPLACE INTO entity_map
                       (entity_id, category, subcategory, room, friendly_name, learned_at)
                       VALUES (?, ?, ?, ?, ?, ?)''',
                    (eid, cat, subcategory, room, fname, datetime.now().isoformat())
                )
                total += 1
            conn.commit()
            conn.close()
            log.info(f"✅ Batch {i//batch_size+1}: {len(results)} categorized")
        except Exception as ex:
            log.error(f"❌ Batch {i//batch_size+1}: {ex}")
        time.sleep(1)

    mem_set("discovery_date", datetime.now().isoformat())
    mem_set("discovery_count", total)
    _refresh_known_entities()


def _refresh_known_entities():
    """Met a day the snapshot of the entities knowns"""
    carto = entity_map_get_all()
    for eid, cat in carto.items():
        known_entities_update(eid, cat)


def compare_entities_on_startup(states):
    """Compare the entities current with the memory_store"""
    knowns = known_entities_get_all()
    if not knowns:
        log.info("First comparison — no history yet")
        return

    current = set(e["entity_id"] for e in states)
    knowns_set = set(knowns.keys())

    missing_entities = knowns_set - current
    for eid in missing_entities:
        cat = knowns.get(eid, "")
        criticality = ENTITY_CRITICALITY.get(cat, {})
        alert_after_h = criticality.get("alert_after_h", 48)
        label = criticality.get("label", cat)
        if alert_after_h <= 4:
            telegram_send(
                f"🚨 MISSING ENTITY — {label}\n{eid}\n"
                f"Not found in Home Assistant at startup."
            )
            log.warning(f"Critical missing entity: {eid}")

    carto_knowns = set(entity_map_get_all().keys())
    new_items = current - carto_knowns
    if new_items:
        log.info(f"🆕 {len(new_items)} new entities to categorize")

    log.info(f"Startup comparison: {len(current)} current, {len(missing_entities)} gone, {len(new_items)} new")


def scan_ha_complete():
    ha_refresh_areas()
    log.info("🔍 Scan HA...")

    states = ha_get("states")
    if not states:
        telegram_send("❌ SCAN — HA unreachable")
        return False

    conn = sqlite3.connect(DB_PATH)
    for e in states:
        conn.execute(
            '''INSERT OR REPLACE INTO entities (entity_id, state, attributes, updated_at)
               VALUES (?, ?, ?, ?)''',
            (e["entity_id"], e["state"],
             json.dumps(e.get("attributes", {})), datetime.now().isoformat())
        )
    conn.commit()
    conn.close()

    mem_set("ha_scan_date", datetime.now().isoformat())
    mem_set("ha_entities_count", len(states))

    threading.Thread(target=discover_automatically, args=(states,), daemon=True).start()

    try:
        role_count = discover_roles(states)
        if role_count > 0:
            log.info(f"🎯 {role_count} role(s) auto-discovered at scan")
    except Exception as ex_r:
        log.error(f"❌ discover_roles: {ex_r}")

    return True


def _detect_new_entities(index):
    """Detection antivirus — flags new entities in under 1ms, 0 token.
    Plugs and power sensors are auto-categorized immediately.
    Other items are reported for the next infiltration scan."""
    # global _entities_already_detected  # via shared

    conn = sqlite3.connect(DB_PATH)
    carto_set = set(r[0] for r in conn.execute("SELECT entity_id FROM entity_map").fetchall())

    domains_ignores = {
        "persistent_notification", "group", "zone", "sun",
        "input_boolean", "input_number", "input_select",
        "input_text", "input_datetime", "timer", "counter",
        "script", "scene", "tag", "device_tracker",
        "automation", "button", "select", "update", "number"
    }

    _PIECES_DETECT = [
        "kitchen", "living_room", "guest bedroom", "child bedroom", "bedroom",
        "laundry_room", "garage", "office", "salle of bain", "sdb",
    ]

    new_items_plugs = []
    new_items_others = []

    for eid, e in index.items():
        if eid in carto_set or eid in _entities_already_detected:
            continue
        domain = eid.split(".")[0]
        if domain in domains_ignores:
            continue

        attrs = e.get("attributes", {})
        fname = attrs.get("friendly_name", eid)
        name_lower = eid.lower()

        room = ha_get_area(eid)
        if not room:
            fn_low = fname.lower()
            for p in _PIECES_DETECT:
                if p in fn_low:
                    room = p
                    break

        dc = attrs.get("device_class", "")
        unit = attrs.get("unit_of_measurement", "")

        _is_plug_par_name = ("plug" in name_lower or "plug" in name_lower
                             or "outlet" in name_lower or "socket" in name_lower)

        _is_plug_par_structure = False
        if domain == "sensor" and (dc == "power" or unit == "W"):
            base = eid.replace("sensor.", "").replace("_power", "").replace("_power", "")
            for candidate in index:
                if candidate.startswith("switch.") and base in candidate:
                    _is_plug_par_structure = True
                    break

        _is_plug = _is_plug_par_name or _is_plug_par_structure
        if _is_plug:
            if domain == "sensor" and (dc == "power" or unit == "W" or name_lower.endswith("_power")):
                conn.execute(
                    "INSERT OR REPLACE INTO entity_map (entity_id, category, subcategory, room, friendly_name, learned_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (eid, "connected_plug", "power", room, fname, datetime.now().isoformat())
                )
                new_items_plugs.append(fname)
            elif domain == "switch" and not name_lower.endswith("_child_lock"):
                conn.execute(
                    "INSERT OR REPLACE INTO entity_map (entity_id, category, subcategory, room, friendly_name, learned_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (eid, "connected_plug", "command", room, fname, datetime.now().isoformat())
                )
                new_items_plugs.append(fname)
            # Others (energy, voltage, current, number, select) → ignore
            _entities_already_detected.add(eid)
            continue

        dc = attrs.get("device_class", "")
        unit = attrs.get("unit_of_measurement", "")
        if domain == "sensor" and (dc == "power" or unit == "W"):
            cat_auto = "energy_consumption"
            if any(k in name_lower for k in ["solar", "solar", "ecu", "inverter", "inverter"]):
                cat_auto = "energy_solar"
            elif any(k in name_lower for k in ["battery", "battery", "solarbank", "anker"]):
                cat_auto = "energy_battery"
            conn.execute(
                "INSERT OR REPLACE INTO entity_map (entity_id, category, subcategory, room, friendly_name, learned_at) VALUES (?, ?, ?, ?, ?, ?)",
                (eid, cat_auto, "power", room, fname, datetime.now().isoformat())
            )
            _entities_already_detected.add(eid)
            new_items_others.append(f"{fname} → {cat_auto}")
            continue

        _entities_already_detected.add(eid)
        dc = attrs.get("device_class", "")
        unit = attrs.get("unit_of_measurement", "")
        state = e.get("state", "?")

        _proto = "unknown"
        _eid_low = eid.lower()
        _fname_low = fname.lower()
        if any(k in _eid_low for k in ("zigbee", "z2m", "zha", "zbee")):
            _proto = "Zigbee"
        elif any(k in _eid_low for k in ("matter", "mtr")):
            _proto = "Matter"
        elif any(k in _eid_low for k in ("zwave", "zw_")):
            _proto = "Z-Wave"
        elif any(k in _eid_low for k in ("esphome", "esp32", "esp8266")):
            _proto = "ESPHome"
        elif any(k in _eid_low for k in ("tapo", "shelly", "tuya", "sonoff", "meross", "wemo", "kasa")):
            _proto = "WiFi"
        elif domain in ("light", "climate", "cover", "fan", "lock", "vacuum"):
            _proto = "HA"
        elif any(k in _eid_low for k in ("hue", "ikea", "tradfri", "aqara", "xiaomi")):
            _proto = "Zigbee"

        # Build the description fcurrentle
        desc_facts = f"{fname}"
        infos = []
        if _proto != "unknown":
            infos.append(_proto)
        if dc:
            infos.append(dc)
        if unit and state not in ("unavailable", "unknown"):
            infos.append(f"{state}{unit}")
        elif state not in ("unavailable", "unknown", ""):
            infos.append(state)
        if room:
            infos.append(f"📍{room}")
        if infos:
            desc_facts += f" ({', '.join(infos)})"

        new_items_others.append(desc_facts)

    if new_items_plugs:
        conn.commit()
        nb_total = conn.execute("SELECT COUNT(*) FROM entity_map").fetchone()[0]
        nb_monitored = conn.execute("SELECT COUNT(*) FROM appliances WHERE monitored=1").fetchone()[0]
        telegram_send(
            f"🔌 DETECTION — {len(new_items_plugs)} new(s) plug(s)\n━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(f"  • {n}" for n in new_items_plugs)
            + f"\n\n📡 Monitoring activated — sniper mode 20s"
            + f"\n📊 {nb_total} entities | {nb_monitored} appliances monitored"
        )
        log.info(f"🔌 Norvelles plugs: {new_items_plugs}")

        # (only the plugs with power sensor not yet in table appliances)
        try:
            new_power = conn.execute(
                "SELECT entity_id, friendly_name FROM entity_map "
                "WHERE category='connected_plug' AND subcategory='power' "
                "AND entity_id NOT IN (SELECT entity_id FROM appliances)"
            ).fetchall()
            if new_power:
                queue = [{"entity_id": eid, "fname": fn} for eid, fn in new_power]
                existing_queue = mem_get("appliances_queue")
                if existing_queue:
                    try:
                        existing = json.loads(existing_queue)
                        existing_eids = {q["entity_id"] for q in existing}
                        queue = [q for q in queue if q["entity_id"] not in existing_eids] + existing
                    except Exception:
                        pass
                mem_set("appliances_queue", json.dumps(queue))
                _ask_question_appliance_next()
        except Exception:
            pass

    if new_items_others:
        conn.commit()
        # Global counter for user reassurance
        nb_total = conn.execute("SELECT COUNT(*) FROM entity_map").fetchone()[0]
        nb_monitored = conn.execute("SELECT COUNT(*) FROM appliances WHERE monitored=1").fetchone()[0]

        msg_new = f"🔍 DETECTED — {len(new_items_others)} new entity/entities\n━━━━━━━━━━━━━━━━━━\n"
        for desc in new_items_others[:8]:
            msg_new += f"  • {desc}\n"
        if len(new_items_others) > 8:
            msg_new += f"  ... +{len(new_items_others) - 8} others\n"
        msg_new += f"\n📊 {nb_total} entities mapped | {nb_monitored} appliances monitored"
        msg_new += f"\n🔄 Automatic categorization in < 1h"
        telegram_send(msg_new)
        log.info(f"🔍 {len(new_items_others)} new_items entities detected")

    conn.close()


def _surface_errors():
    """SKILL AUTO-GUERISON — Pipeline ferme, 0 intervention user.

    Cycle complete :
    1. CAPTURE : _ErrorCaptureHandler intercepte log.error()
    2. TRIAGE : group by signature, anti-spam 6h
    3. DIAGNOSTIC: if error ≥ 3x/1h → AI-assisted auto-correction
    4. CORRECTION: patch applied + restart WITHOUT asking
    5. VERIFICATION: if error recurs after fix → rollback
    6. NOTIFICATION: 1 summary message only — never spam

    The user sees NOTHING. Ever. Errors are the script's problem, not the user's.
    """
    # # global _errors_buffer, _errors_seen    # via shared# via shared

    if not _errors_buffer:
        return

    # Copier and vider the buffer
    errors = _errors_buffer.copy()
    _errors_buffer.key_namear()

    # Group by signature
    groupes = {}
    for ts, msg, sig in errors:
        if sig not in groupes:
            groupes[sig] = {"count": 0, "first_ts": ts, "last_ts": ts, "msg": msg}
        groupes[sig]["count"] += 1
        groupes[sig]["last_ts"] = ts

    now = datetime.now()

    h1 = (now - timedelta(hours=1)).isoformat()
    h24 = (now - timedelta(hours=24)).isoformat()

    for sig, info in groupes.items():
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "INSERT INTO decisions_log (action, context, result, success, created_at) VALUES (?, ?, ?, 0, ?)",
                ("ERROR_AUTO", json.dumps({"sig": sig[:80], "n": info["count"]}, ensure_ascii=False),
                 info["msg"][:200], now.isoformat())
            )
            conn.commit()

            nb_1h = conn.execute(
                "SELECT COUNT(*) FROM decisions_log WHERE action='ERROR_AUTO' AND context LIKE ? AND created_at > ?",
                (f"%{sig[:40]}%", h1)
            ).fetchone()[0]

            existing_fix = conn.execute(
                "SELECT COUNT(*) FROM decisions_log WHERE action='AUTO_FIX_OK' AND context LIKE ? AND created_at > ?",
                (f"%{sig[:40]}%", h24)
            ).fetchone()[0]

            already_tried = conn.execute(
                "SELECT COUNT(*) FROM decisions_log WHERE action='AUTO_FIX_FAIL' AND context LIKE ? AND created_at > ?",
                (f"%{sig[:40]}%", h24)
            ).fetchone()[0]

            conn.close()
        except Exception:
            nb_1h = info["count"]
            existing_fix = 0
            already_tried = 0

        if nb_1h < 3:
            continue  # Not yet recurring → silence

        if existing_fix > 0:
            continue  # Already fixed → no loop

        if already_tried > 0:
            continue  # Already tried and failed → wait 24h

        # Anti-spam only on the action (not the comptage)
        last_action = _errors_seen.get(sig)
        if last_action:
            try:
                if (now - datetime.fromisoformat(last_action)).total_seconds() < 3600:
                    continue
            except Exception:
                pass
        _errors_seen[sig] = now.isoformat()

        # ═══ AUTO-CORRECTION ═══
        log.info(f"🔧 Auto-heal: {nb_1h} occurrences/1h → correction: {sig[:60]}")

        if not check_budget():
            continue

        try:
            result = _auto_heal(sig, info["msg"])
            action_db = "AUTO_FIX_OK" if result == "OK" else "AUTO_FIX_FAIL"
            try:
                conn2 = sqlite3.connect(DB_PATH)
                conn2.execute(
                    "INSERT INTO decisions_log (action, context, result, success, created_at) VALUES (?, ?, ?, ?, ?)",
                    (action_db, json.dumps({"sig": sig[:80]}, ensure_ascii=False),
                     result or "failure", 1 if result == "OK" else 0, now.isoformat())
                )
                conn2.commit()
                conn2.close()
            except Exception:
                pass
            if result == "FAIL":
                _auto_heal(sig, info["msg"], occurrence_count, retry=True)
        except Exception:
            pass

    # Clean the vieilles signatures
    cutoff = (now - timedelta(hours=24)).isoformat()
    _errors_seen = {k: v for k, v in _errors_seen.items() if v > cutoff}


def _auto_heal(signature, message_error, occurrence_count=2, retry=False):
    """Autonomous correction: the configured strong model proposes a patch, applies it, restarts."""

    msg_clean = message_error
    if "] " in msg_clean:
        msg_clean = msg_clean.split("] ", 1)[-1]

    # 1. Read the script.
    try:
        cfg_secret = CFG.get("deploy_secret", "")
        req_r = urllib.request.Request("http://localhost:8501/read")
        req_r.add_header("Authorization", f"Bearer {cfg_secret}")
        resp_r = urllib.request.urlopen(req_r, timeout=15)
        script_data = json.loads(resp_r.read().decode())
        script_code = script_data["content"]
        script_lines = script_data["lines"]
    except Exception as e:
        log.error(f"auto-heal: script read: {e}")
        return "FAIL"

    # 2. Read the latest error logs (more context on retry)
    nb_logs = 100 if retry else 30
    try:
        req_l = urllib.request.Request(f"http://localhost:8501/logs?n={nb_logs}")
        req_l.add_header("Authorization", f"Bearer {cfg_secret}")
        resp_l = urllib.request.urlopen(req_l, timeout=10)
        all_logs = json.loads(resp_l.read().decode()).get("lines", [])
        # Filter errors and nearby context.
        recent_logs = "\n".join([l for l in all_logs if "ERROR" in l or "error" in l.lower()][-20:])
        if not recent_logs:
            recent_logs = "\n".join(all_logs[-15:])
    except Exception:
        recent_logs = message_error

    # 3. Extract the relevant context without sending the whole script.
    # Search the lines that contain the error pattern
    error_words = [m for m in msg_clean.split() if len(m) > 4][:5]
    script_lines_list = script_code.split("\n")
    relevant_lines = set()
    for i, line in enumerate(script_lines_list):
        if any(word in line for word in error_words):
            for j in range(max(0, i-30), min(len(script_lines_list), i+30)):
                relevant_lines.add(j)

    if not relevant_lines:
        context = "\n".join(f"L{i+1}: {l}" for i, l in enumerate(script_lines_list[:500]))
        context += "\n...\n"
        context += "\n".join(f"L{i+1}: {l}" for i, l in enumerate(script_lines_list[-500:], len(script_lines_list)-500))
    else:
        indices = sorted(relevant_lines)
        context = "\n".join(f"L{i+1}: {script_lines_list[i]}" for i in indices)

    try:
        patch_prompt = (
            "You are the self-healing system of a Python script (assistant.py).\n"
            "An error is blocking the script. You MUST fix it.\n\n"
            "METHOD:\n"
            "1. Read the error message — identify the variable/function/line that breaks\n"
            "2. Find that line in the script\n"
            "3. Propose a MINIMAL fix (try/except, default value, guard clause)\n\n"
            "FORMAT — raw JSON only:\n"
            '{"old_str": "exact_code_to_replace", "new_str": "new_code", "explanation": "what_yor_are_fixing"}\n\n'
            "RULES:\n"
            "- old_str = EXACT copy (sheets, indentation, quotes identical to script)\n"
            "- old_str must appear exactly 1 TIME\n"
            "- Change the MINIMUM — a try/except or 'if x:' is often enough\n"
            "- NO markdown, NO ```, NO text before/after\n"
            + ("\nRETRY WARNING: the first patch failed because old_str did not match. "
               "Be MORE PRECISE — copy the code EXACTLY as it appears in the script, "
               "including every indentation sheet.\n" if retry else "")
        )
        user_patch = (
            f"RECURRING ERROR ({occurrence_count}x in 60s):\n{msg_clean[:300]}\n\n"
            f"RECENT LOGS:\n{recent_logs[:1000]}\n\n"
            f"SCRIPT CONTEXT (relevant lines):\n{context[:15000]}"
        )
        blocks, t_in, t_out = llm_provider.llm_completion(
            CFG, [{"role": "user", "content": user_patch}],
            model=llm_provider.get_model(CFG, use_strong=True),
            max_tokens=4000,
            system_prompt=patch_prompt
        )
        response = llm_provider.stream_text(blocks).strip()
        log_token_usage(t_in, t_out)
    except Exception as e:
        log.error(f"auto-heal: LLM: {e}")
        return "FAIL"

    try:
        text = response.replace("```json", "").replace("```", "").strip()
        idx_start = text.find("{")
        idx_end = text.rfind("}") + 1
        if idx_start >= 0 and idx_end > idx_start:
            text = text[idx_start:idx_end]
        patch = json.loads(text)
        old_str = patch.get("old_str", "")
        new_str = patch.get("new_str", "")
        explanation = patch.get("explanation", "")
    except Exception as ex_json:
        log.error(f"auto-heal: invalid JSON ({ex_json}) — response: {response[:200]}")
        return "FAIL"

    if not old_str:
        log.info(f"auto-heal: LLM cannot fix — {explanation[:100]}")
        return "SKIP"

    if script_code.count(old_str) != 1:
        log.error(f"auto-heal: old_str found {script_code.count(old_str)} times")
        return "FAIL"

    try:
        payload = json.dumps({"mode": "replace", "old_str": old_str, "new_str": new_str}).encode()
        sig = hmac.new(cfg_secret.encode(), payload, hashlib.sha256).hexdigest()
        req_p = urllib.request.Request("http://localhost:8501/patch", data=payload, method="POST")
        req_p.add_header("Content-Type", "application/json")
        req_p.add_header("Authorization", f"HMAC {sig}")
        resp_p = urllib.request.urlopen(req_p, timeout=15)
        result = json.loads(resp_p.read().decode())
        if result.get("status") != "ok":
            log.error(f"auto-heal: patch failed: {result}")
            return "FAIL"
    except Exception as e:
        log.error(f"auto-heal: patch: {e}")
        return "FAIL"

    # 7. Log silent
    log.info(f"🔧 Auto-heal: {explanation[:150]} — restart")

    # 8. Restart
    try:
        payload_r = json.dumps({"action": "restart"}).encode()
        sig_r = hmac.new(cfg_secret.encode(), payload_r, hashlib.sha256).hexdigest()
        req_restart = urllib.request.Request("http://localhost:8501/restart", data=payload_r, method="POST")
        req_restart.add_header("Content-Type", "application/json")
        req_restart.add_header("Authorization", f"HMAC {sig_r}")
        urllib.request.urlopen(req_restart, timeout=15)
    except Exception:
        pass

    return "OK"

    try:
        text = response.replace("```json", "").replace("```", "").strip()
        idx_start = text.find("{")
        idx_end = text.rfind("}") + 1
        if idx_start >= 0 and idx_end > idx_start:
            text = text[idx_start:idx_end]
        patch = json.loads(text)
        old_str = patch.get("old_str", "")
        new_str = patch.get("new_str", "")
        explanation = patch.get("explanation", "")
    except Exception as ex_json:
        log.error(f"auto-heal: invalid JSON ({ex_json}) — response: {response[:200]}")
        return "FAIL"

    if not old_str:
        log.info(f"auto-heal: LLM cannot fix — {explanation[:100]}")
        return "SKIP"

    if script_code.count(old_str) != 1:
        log.error(f"auto-heal: old_str found {script_code.count(old_str)} times")
        return "FAIL"

    try:
        payload = json.dumps({"mode": "replace", "old_str": old_str, "new_str": new_str}).encode()
        sig = hmac.new(cfg_secret.encode(), payload, hashlib.sha256).hexdigest()
        req_p = urllib.request.Request("http://localhost:8501/patch", data=payload, method="POST")
        req_p.add_header("Content-Type", "application/json")
        req_p.add_header("Authorization", f"HMAC {sig}")
        resp_p = urllib.request.urlopen(req_p, timeout=15)
        result = json.loads(resp_p.read().decode())
        if result.get("status") != "ok":
            log.error(f"auto-heal: patch failed: {result}")
            return "FAIL"
    except Exception as e:
        log.error(f"auto-heal: patch: {e}")
        return "FAIL"

    # 7. Log silent
    log.info(f"🔧 Auto-heal: {explanation[:150]} — restart")

    # 8. Restart
    try:
        payload_r = json.dumps({"action": "restart"}).encode()
        sig_r = hmac.new(cfg_secret.encode(), payload_r, hashlib.sha256).hexdigest()
        req_restart = urllib.request.Request("http://localhost:8501/restart", data=payload_r, method="POST")
        req_restart.add_header("Content-Type", "application/json")
        req_restart.add_header("Authorization", f"HMAC {sig_r}")
        urllib.request.urlopen(req_restart, timeout=15)
    except Exception:
        pass

    return "OK"


def cmd_roi():
    """Show the ROI : tokens spent vs generated savings.
    ROI is the key metric. If ROI > 1, each token is profitable.
    This number justifies the business model."""
    conn = sqlite3.connect(DB_PATH)
    month = datetime.now().strftime("%Y-%m")

    tokens_row = conn.execute(
        "SELECT tokens_in, tokens_out FROM tokens WHERE month=?", (month,)
    ).fetchone()
    total_tokens = (tokens_row[0] + tokens_row[1]) if tokens_row else 0
    cost_tokens = round(total_tokens * 0.000001, 2)  # lightweight model ~$1/1M

    cycles_sol = conn.execute(
        "SELECT COUNT(*), SUM(consumption_kwh), SUM(cost_eur) FROM appliance_cycles "
        "WHERE solar_production_w > 500 AND ended_at IS NOT NULL AND created_at LIKE ?",
        (f"{month}%",)
    ).fetchall()

    recommendation_data, _ = skill_get("recommendations")
    conn.close()

    eco_data = get_savings_month(month)
    actual_savings = eco_data["total_eur"]
    eco_kwh = eco_data["total_kwh"]
    nb_actions = eco_data["nb_actions"]

    report = f"📈 ROI ASSISTANT AI — {month}\n━━━━━━━━━━━━━━━━━━\n"

    report += f"\n🔑 INVESTMENT\n  {total_tokens:,} tokens | {cost_tokens:.2f}€\n"

    report += f"\n💰 REAL SAVINGS ({nb_actions} actions)\n"
    for saving_type, info in eco_data.get("by_type", {}).items():
        type_labels = {
            "cycle_solar": "☀️ Solar cycles",
            "rate_optimal": "⚡ Rate optimization",
            "surconsumption_evitee": "📉 Avoided overconsumption",
            "recommendation_applied": "💡 Recommendations",
        }
        label = type_labels.get(saving_type, saving_type)
        report += f"  {label} : +{info['eur']:.2f}€ ({info['nb']}x)\n"
    report += f"  ━━━\n  Total measured: {actual_savings:.2f}€ | {eco_kwh:.1f} kWh\n"

    eco_potential = 0
    if recommendation_data and "recommendations" in recommendation_data:
        eco_potential = sum(r.get("saving_month_eur", 0) for r in recommendation_data["recommendations"])
        score = recommendation_data.get("optimization_score", 0)
        report += f"\n💡 UNUSED POTENTIAL\n"
        for r in recommendation_data["recommendations"][:3]:
            report += f"  • {r.get('action', '?')} → ~{r.get('saving_month_eur', 0):.0f}€/month\n"
        report += f"  Optimization score : {score}/100\n"

    report += f"\n━━━━━━━━━━━━━━━━━━\n"
    report += f"📊 THE VIRTUOUS CYCLE\n"
    report += f"  Tokens    : {cost_tokens:.2f}€/month\n"
    report += f"  Savings: {actual_savings:.2f}€ (actual) + ~{eco_potential:.0f}€ (potential)\n"
    total_eco = actual_savings + eco_potential
    if cost_tokens > 0:
        roi = total_eco / cost_tokens
        report += f"  ROI       : x{roi:.1f}\n"
        if roi >= 5:
            report += "  ✅ Every €1 of tokens returns {:.0f}€ in savings\n".format(roi)
        elif roi >= 1:
            report += "  🟡 Profitable — expertise is growing\n"
        else:
            report += "  🔴 In progress - baselines are accumulating\n"
    else:
        report += "  ROI : ∞ (0€ of tokens this month)\n"

    report += f"\n💡 The more the script learns, the fewer tokens it uses,\n   and the more it saves. That's the virtuous cycle."
    report += f"\n\n💡 The more the AI learns, the higher the ROI."

    return report


def skill_window_solar(states):
    """Learn typical solar production by weekday/hour to drive proactive suggestions."""
    try:
        if not states:
            return

        now = datetime.now()
        # Ignore deep-night slots to avoid noise dominated by zeros.
        if now.hour < 6 or now.hour > 20:
            return

        bucket_key = f"{now.strftime('%Y-%m-%d')}-{now.hour}-{now.minute // 15}"
        if mem_get("window_solar_last_bucket") == bucket_key:
            return
        mem_set("window_solar_last_bucket", bucket_key)

        production_w = ha_get_current_solar_production(states)
        if production_w is None:
            return
        production_w = max(0.0, float(production_w))

        data, nb = skill_get("window_solar")
        if not isinstance(data, dict):
            data = {}

        day_key = str(now.weekday())
        hour_key = str(now.hour)
        day_slots = data.get(day_key, {})
        slot = day_slots.get(hour_key, [0.0, 0])
        try:
            old_avg = float(slot[0])
            old_count = int(slot[1])
        except Exception:
            old_avg = 0.0
            old_count = 0

        new_count = old_count + 1
        new_avg = ((old_avg * old_count) + production_w) / new_count
        day_slots[hour_key] = [round(new_avg, 2), new_count]
        data[day_key] = day_slots

        skill_set("window_solar", data, (nb or 0) + 1)
    except Exception as ex:
        log.debug(f"skill_window_solar: {ex}")


def skill_suggestion_machine(states):
    """Suggest the best time to run an appliance based on the solar window.
    Without solar sensors, reminders still work but solar suggestions are disabled."""
    now = datetime.now()
    day = now.weekday()
    hour = now.hour

    reminder = mem_get("reminder_machine")
    if reminder:
        try:
            dt_reminder = datetime.fromisoformat(reminder)
            if now >= dt_reminder:
                hour_str = mem_get("reminder_machine_hour", f"{dt_reminder.hour}h{dt_reminder.minute:02d}")
                production_w = ha_get_current_solar_production(states)
                mem_set("reminder_machine", "")
                mem_set("reminder_machine_hour", "")

                if production_w == 0:
                    try:
                        data_sol, nb_sol = skill_get("window_solar")
                        day_str = str(now.weekday())
                        hour_reminder = str(now.hour)
                        if data_sol and day_str in data_sol and hour_reminder in data_sol[day_str]:
                            prod_attendue = data_sol[day_str][hour_reminder][0]
                            if prod_attendue > 500:
                                production_w = int(prod_attendue)  # Utiliser the prediction
                    except Exception:
                        pass

                if production_w > 500:
                    telegram_send(
                        f"⏰ APPLIANCE REMINDER — {hour_str}\n"
                        f"☀️ Solar production: {int(production_w)} W\n"
                        f"This is a good time to start the appliance."
                    )
                else:
                    telegram_send(
                        f"⏰ APPLIANCE REMINDER — {hour_str}\n"
                        f"☁️ Solar production is low: {int(production_w)} W\n"
                        f"You can start anyway or wait for clearer skies."
                    )
                log.info(f"⏰ Appliance reminder triggered: {hour_str}, prod={int(production_w)}W")
        except Exception:
            mem_set("reminder_machine", "")

    if day not in MACHINE_DAYS:
        return
    if hour < 8 or hour > 16:
        return

    last_suggestion = mem_get("last_suggestion_machine")
    if last_suggestion:
        try:
            dt = datetime.fromisoformat(last_suggestion)
            if (now - dt).total_seconds() < 12 * 3600:
                return
        except Exception:
            pass

    data, nb = skill_get("window_solar")
    if not data or nb < 20:
        return  # Not enough learning

    day_str = str(day)
    if day_str not in data:
        return

    best_hour = None
    best_prod = 0
    for h_str, (avg, n) in data[day_str].items():
        if n >= 3 and avg > best_prod:
            h_int = int(h_str)
            if h_int >= hour and h_int <= 16:  # Creneaux futurs only
                best_prod = avg
                best_hour = h_int

    if best_hour is None or best_prod < 500:
        return  # No slot solar interesting

    production_w = ha_get_current_solar_production(states)

    if hour == best_hour and production_w >= best_prod * 0.6:
        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

        price_now = rate_current_kwh_price()
        rate = rate_get()
        ttype = rate.get("type", "base")

        info_rate = ""
        if ttype in ("weekend", "weekend_hphc", "weekend_plus", "weekend_plus_hphc"):
            if _is_weekend_or_holiday() or _est_chosen_day(rate):
                info_rate = f"\n💰 Reduced rate (weekend/selected day): {price_now}/kWh"
            else:
                info_rate = f"\n💰 Weekday rate: {price_now}/kWh"
        elif ttype == "hphc":
            is_off_peak = _is_off_peak_hour_ranges(rate.get("off_peak_hours", []))
            info_rate = f"\n💰 {'Off-peak' if is_off_peak else 'Peak'}: {price_now}/kWh"

        cov_pct = min(100, int(production_w / 2000 * 100))
        part_grid = max(0, 100 - cov_pct) / 100
        estimated_cost = round(1.5 * price_now * part_grid, 2)
        solar_saving = round(1.5 * price_now - estimated_cost, 2)
        info_eco = f"\n💡 ~{cov_pct}% solar → ~{estimated_cost} grid, ~{solar_saving} saved"

        telegram_send(
            f"☀️ GOOD APPLIANCE WINDOW\n"
            f"Solar production: {int(production_w)} W\n"
            f"Optimal slot ({days[day]} {best_hour}h) : ~{int(best_prod)} W typical"
            f"{info_rate}{info_eco}\n\n"
            f"Reply with the time (e.g.: 12h30) or ❌"
        )
        mem_set("last_suggestion_machine", now.isoformat())
        mem_set("pending_hour_machine", "yes")
        log.info(f"☀️ Appliance suggestion: {best_hour}h, {int(best_prod)}W, {price_now}/kWh")


def cmd_skills():
    """Shows the status of learned skills"""
    report = "🧠 AUTONOMOUS SKILLS\n━━━━━━━━━━━━━━━━━━\n"

    data_sol, nb_sol = skill_get("window_solar")
    report += f"\n☀️ SOLAR WINDOW ({nb_sol} learning samples)\n"
    if data_sol and nb_sol >= 10:
        days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        for j in range(7):
            j_str = str(j)
            if j_str in data_sol:
                hours = data_sol[j_str]
                if hours:
                    best_h = max(hours.items(), key=lambda x: x[1][0])
                    report += f"  {days[j]}: peak at {best_h[0]}h → {int(best_h[1][0])} W ({best_h[1][1]} measurements)\n"
    else:
        report += "  Still learning...\n"

    # 2. Appliance signatures
    data_cyc, nb_cyc = skill_get("cycle_signatures")
    report += f"\n🔄 SIGNATURES MACHINES ({nb_cyc} learning samples)\n"
    if data_cyc:
        for eid, info in data_cyc.items():
            report += (
                f"  {info['name']} : {info['duration_avg']:.0f} min | "
                f"{info['consumption_avg']:.2f} kWh | {info['power_avg']:.0f} W avg | "
                f"{info['nb_cycles']} cycles\n"
            )
    else:
        report += "  No cycles recorded yet\n"

    # 3. Heat pump behavior
    heat_pump_data, heat_pump_count = skill_get("heat_pump_behavior")
    report += f"\n🌡️ HEAT PUMP BEHAVIOR ({heat_pump_count} learning samples)\n"
    if heat_pump_data and "tranches" in heat_pump_data:
        tranches = heat_pump_data["tranches"]
        for temp in sorted(tranches.keys(), key=lambda x: float(x)):
            t = tranches[temp]
            total = t["heat_pump_on"] + t["heat_pump_off"]
            if total >= 5:
                pct_on = int(t["heat_pump_on"] / total * 100)
                report += f"  {temp}°C : heat pump ON {pct_on}% | Consumption avg {t['consumption_avg']:.0f} W ({total} samples)\n"
    else:
        report += "  Still learning...\n"

    if nb_sol < 20:
        report += "\n💡 Skills build over time.\nAppliance suggestions active after ~1 week."

    conn = sqlite3.connect(DB_PATH)
    dyn_rows = conn.execute(
        "SELECT name, data, nb_learning samples FROM skills WHERE name LIKE 'dyn_%'"
    ).fetchall()
    conn.close()

    if dyn_rows:
        report += f"\n🤖 DYNAMIC SKILLS ({len(dyn_rows)})\n"
        for name, data_json, nb in dyn_rows:
            try:
                definition = json.loads(data_json)
                desc = definition.get("description", name)
                action = definition.get("action", "?")
                entities = definition.get("entities", [])
                hist_len = len(definition.get("history", []))
                report += f"  {name} : {desc}\n"
                report += f"    Action: {action} | Entities: {len(entities)} | Points: {hist_len}\n"
            except Exception:
                report += f"  {name} : (error lecture)\n"
    else:
        report += "\n🤖 DYNAMIC SKILLS: noe yet (created automatically)\n"

    return report


def send_md_par_email():
    """Send the specification by email as an attachment"""
    md_path = os.path.join(os.path.dirname(DB_PATH), "SPECIFICATION.md")
    if not os.path.exists(md_path):
        return False
    try:
        with open(md_path, "r") as f:
            content = f.read()
        ok = send_email(
            f"[AI Companion] Specification — {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            f"Automatic update of the Specification.\n"
            f"Version current : {len(content.split(chr(10)))} lines, {len(content)//1024} KB.\n\n"
            f"This file contains the project summary instructions.\n"
            f"Paste the RESUME section into a new AI conversation.",
            attachment=md_path
        )
        if ok:
            log.info("📧 MD sent by email")
        return ok
    except Exception as e:
        log.error(f"❌ Send MD email: {e}")
        return False


def check_markdown_change():
    """Check whether the Markdown file changed; send it by email if needed"""
    # global _md_last_hash  # via shared
    md_path = os.path.join(os.path.dirname(DB_PATH), "SPECIFICATION.md")
    if not os.path.exists(md_path):
        return
    try:
        with open(md_path, "rb") as f:
            h = hashlib.md5(f.read()).hexdigest()
        if _md_last_hash is None:
            shared._md_last_hash = h  # First run — no send
            return
        if h != _md_last_hash:
            shared._md_last_hash = h
            log.info("📄 MD modified — sending by email")
            send_md_par_email()
    except Exception:
        pass


def generate_cognitive_hypotheses(states, index):
    """Generate testable hypotheses from the data.
    A hypothesis is a verifiable prediction about home behavior."""

    conn = sqlite3.connect(DB_PATH)

    # Limit active hypotheses
    active_count = conn.execute("SELECT COUNT(*) FROM hypotheses WHERE active=1").fetchone()[0]
    if active_count >= 15:
        conn.close()
        return

    now = datetime.now()
    day = now.weekday()
    hour = now.hour
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

    baselines = {}
    for eid, label in BASELINE_ENTITIES.items():
        row = conn.execute(
            "SELECT avg_value, sample_count FROM baselines WHERE entity_id=? AND weekday=? AND hour=?",
            (eid, day, hour)
        ).fetchone()
        if row and row[1] >= 5:
            baselines[label] = row[0]

    data_sol, nb_sol = skill_get("window_solar")

    # Load heat pump
    heat_pump_data, heat_pump_count = skill_get("heat_pump_behavior")

    hypotheses_candidates = []

    if data_sol and nb_sol >= 20:
        day_str = str(day)
        if day_str in data_sol:
            slotx = data_sol[day_str]
            if slotx:
                best = max(slotx.items(), key=lambda x: x[1][0])
                best_h = int(best[0])
                best_w = best[1][0]

                tomorrow = (day + 1) % 7
                tomorrow_str = str(tomorrow)
                if tomorrow_str in data_sol:
                    slotx_d = data_sol[tomorrow_str]
                    if slotx_d:
                        best_d = max(slotx_d.items(), key=lambda x: x[1][0])
                        hypotheses_candidates.append({
                            "statement": f"Tomorrow {days[tomorrow]} at {best_d[0]}h, solar production > {int(best_d[1][0] * 0.7)}W",
                            "category": "solar",
                            "condition_test": json.dumps({
                                "type": "value_text_min",
                                "entity_id": role_get("solar_production_w") or "sensor.ecu_current_power",
                                "day": tomorrow,
                                "hour": int(best_d[0]),
                                "threshold": best_d[1][0] * 0.7
                            })
                        })

    if heat_pump_data and heat_pump_count >= 20:
        temp_ext = None
        e_ext = index.get(role_get("outdoor_temperature") or "sensor.ecojoko_outdoor_temperature")
        if e_ext and e_ext["state"] not in ("unavailable", "unknown"):
            try:
                temp_ext = float(e_ext["state"])
            except Exception:
                pass

        if temp_ext is not None:
            tranche = str(round(temp_ext / 2) * 2)
            tranches = heat_pump_data.get("tranches", {})
            if tranche in tranches:
                t = tranches[tranche]
                total = t["heat_pump_on"] + t["heat_pump_off"]
                if total >= 5:
                    pct_on = t["heat_pump_on"] / total
                    if pct_on > 0.7:
                        hypotheses_candidates.append({
                            "statement": f"With {temp_ext:.0f}°C outside, heat pump should be ON ({pct_on:.0%} historical)",
                            "category": "heat_pump",
                            "condition_test": json.dumps({
                                "type": "state_attendu",
                                "entity_pattern": "climate.",
                                "valid_states": ["auto", "heat", "cool", "fan_only", "heat_cool"],
                                "temp_ext_range": [temp_ext - 2, temp_ext + 2]
                            })
                        })

    consumption_baseline = baselines.get("grid_consumption_w")
    if consumption_baseline and consumption_baseline > 100:
        hypotheses_candidates.append({
            "statement": f"Grid consumption {days[day]} {hour}h should be ~{consumption_baseline:.0f}W ±40%",
            "category": "energy",
            "condition_test": json.dumps({
                "type": "value_text_range",
                "entity_id": role_get("realtime_consumption") or "sensor.ecojoko_realtime_consumption",
                "min": consumption_baseline * 0.6,
                "max": consumption_baseline * 1.4,
                "day": day,
                "hour": hour
            })
        })

    for h in hypotheses_candidates:
        existing = conn.execute(
            "SELECT id FROM hypotheses WHERE statement LIKE ? AND active=1",
            (f"%{h['statement'][:50]}%",)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO hypotheses (statement, category, condition_test, confidence, active, created_at, updated_at) "
                "VALUES (?, ?, ?, 0.5, 1, ?, ?)",
                (h["statement"], h["category"], h["condition_test"],
                 now.isoformat(), now.isoformat())
            )

    conn.commit()
    conn.close()


def test_cognitive_hypotheses(states, index):
    """Test active hypotheses against observed reality.
    Each test strengthens or weakens confidence."""

    conn = sqlite3.connect(DB_PATH)
    now = datetime.now()
    day = now.weekday()
    hour = now.hour

    hypotheses = conn.execute(
        "SELECT id, statement, condition_test, confidence, predictions, confirmations, refutations "
        "FROM hypotheses WHERE active=1"
    ).fetchall()

    for h_id, statement, cond_json, confidence, predictions, confirmations, refutations in hypotheses:
        try:
            cond = json.loads(cond_json)
            cond_type = cond.get("type", "")
            result = None  # None = not testable now, True = confirmed, False = refuted

            if cond_type == "value_text_min":
                if cond.get("day") == day and cond.get("hour") == hour:
                    eid = cond["entity_id"]
                    e = index.get(eid)
                    if e and e["state"] not in ("unavailable", "unknown"):
                        try:
                            val = float(e["state"])
                            result = val >= cond["threshold"]
                        except Exception:
                            pass

            elif cond_type == "value_text_range":
                if cond.get("day") == day and cond.get("hour") == hour:
                    eid = cond["entity_id"]
                    e = index.get(eid)
                    if e and e["state"] not in ("unavailable", "unknown"):
                        try:
                            val = float(e["state"])
                            result = cond["min"] <= val <= cond["max"]
                        except Exception:
                            pass

            elif cond_type == "state_attendu":
                pattern = cond.get("entity_pattern", "")
                valid_states = cond.get("valid_states", [])
                for eid, e in index.items():
                    if pattern in eid:
                        carto = entity_map_get(eid)
                        if carto and "heating" in carto[0]:
                            result = e["state"] in valid_states
                            break

            if result is not None:
                predictions += 1
                if result:
                    confirmations += 1
                    new_conf = min(1.0, confidence + 0.05)
                else:
                    refutations += 1
                    new_conf = max(0.0, confidence - 0.1)

                conn.execute(
                    "UPDATE hypotheses SET predictions=?, confirmations=?, refutations=?, "
                    "confidence=?, updated_at=? WHERE id=?",
                    (predictions, confirmations, refutations, round(new_conf, 3),
                     now.isoformat(), h_id)
                )

                if new_conf < 0.15 and predictions >= 5:
                    conn.execute("UPDATE hypotheses SET active=0 WHERE id=?", (h_id,))
                    log.info(f"❌ Hypothesis dropped: {statement[:60]}")

                if new_conf > 0.85 and predictions >= 10:
                    existing_exp = conn.execute(
                        "SELECT id FROM expertise WHERE insight LIKE ?",
                        (f"%{statement[:40]}%",)
                    ).fetchone()
                    if not existing_exp:
                        # Cap 50
                        _nb_exp = conn.execute("SELECT COUNT(*) FROM expertise").fetchone()[0]
                        if _nb_exp >= 50:
                            conn.execute(
                                "DELETE FROM expertise WHERE id = ("
                                "SELECT id FROM expertise WHERE source NOT LIKE 'founding_lesson%' "
                                "ORDER BY confidence ASC LIMIT 1)")
                        conn.execute(
                            "INSERT INTO expertise (category, insight, confidence, nb_validations, source, created_at, updated_at) "
                            "VALUES (?, ?, ?, ?, 'hypothesis_validated', ?, ?)",
                            (cond.get("category", "general"),
                             f"VALIDE: {statement}",
                             new_conf, predictions,
                             now.isoformat(), now.isoformat())
                        )
                        log.info(f"★ Hypothesis promoted to expertise: {statement[:60]}")

        except Exception as ex:
            log.error(f"❌ Hypothesis test {h_id}: {ex}")

    conn.commit()
    conn.close()


def cognitif_calculer_score():
    """Calculates the global intelligence score — measures system growth."""
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    expertise_count = conn.execute("SELECT COUNT(*) FROM expertise").fetchone()[0]
    hypothesis_count = conn.execute("SELECT COUNT(*) FROM hypotheses WHERE active=1").fetchone()[0]

    hyp_stats = conn.execute(
        "SELECT SUM(predictions), SUM(confirmations) FROM hypotheses WHERE predictions > 0"
    ).fetchone()
    total_pred = hyp_stats[0] or 0
    total_conf = hyp_stats[1] or 0
    prediction_rate = (total_conf / total_pred * 100) if total_pred > 0 else 0

    skill_count = conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
    baseline_count = conn.execute("SELECT COUNT(*) FROM baselines").fetchone()[0]

    today_start = now.replace(hour=0, minute=0, second=0).isoformat()
    nb_failures = conn.execute(
        "SELECT COUNT(*) FROM decisions_log WHERE success=0 AND created_at > ?", (today_start,)
    ).fetchone()[0]
    nb_success = conn.execute(
        "SELECT COUNT(*) FROM decisions_log WHERE success=1 AND created_at > ?", (today_start,)
    ).fetchone()[0]

    cycles_solar = conn.execute(
        "SELECT COUNT(*), SUM(consumption_kwh) FROM appliance_cycles "
        "WHERE solar_production_w > 500 AND ended_at IS NOT NULL"
    ).fetchone()
    nb_cycles_sol = cycles_solar[0] or 0
    kwh_solar = cycles_solar[1] or 0
    saving = round(kwh_solar * 0.22, 2)  # ~0.22€/kWh blue rate

    # ═══ CALCUL SCORE ═══
    # Composite score: each dimension contributes
    score = 0.0

    avg_conf = conn.execute("SELECT AVG(confidence) FROM expertise").fetchone()[0] or 0
    score += min(25, expertise_count * avg_conf * 2)

    score += min(25, prediction_rate * 0.25)

    # Coverage (0-25 pts) : baselines + skills
    coverage = min(168 * 5, baseline_count) / (168 * 5) * 100  # 168 slotx × 5 entities
    score += min(25, coverage * 0.25)

    total_decisions = nb_failures + nb_success
    resilience_pts = 0
    if total_decisions > 0:
        resilience = nb_success / total_decisions * 100
        resilience_pts = min(25, resilience * 0.25)
        score += resilience_pts
    else:
        resilience_pts = 12.5
        score += resilience_pts  # Neutral when no data is available

    score = round(score, 1)

    # Niveau
    if score >= 80:
        level = "🏆 EXPERT"
    elif score >= 60:
        level = "🥇 AVANCE"
    elif score >= 40:
        level = "🥈 INTERMEDAIIRE"
    elif score >= 20:
        level = "🥉 DEBUTANT"
    else:
        level = "🌱 INITAIL"

    # Store
    conn.execute(
        "INSERT OR REPLACE INTO intelligence_score "
        "(date, score_global, expertise_count, active_hypothesis_count, prediction_rate, "
        "skill_count, baseline_count, daily_failure_count, daily_success_count, estimated_savings, details) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (today, score, expertise_count, hypothesis_count, round(prediction_rate, 1),
         skill_count, baseline_count, nb_failures, nb_success, saving,
         json.dumps({"level": level, "coverage": round(coverage, 1)}, ensure_ascii=False))
    )
    conn.commit()
    conn.close()

    return {
        "score": score, "level": level,
        "expertise": expertise_count, "hypotheses": hypothesis_count,
        "prediction_rate": prediction_rate, "skills": skill_count,
        "baselines": baseline_count, "coverage": coverage,
        "failures_day": nb_failures, "success_day": nb_success,
        "resilience_pts": resilience_pts, "saving": saving
    }


def cmd_intelligence():
    """Complete dashboard — intelligence score + evolution"""
    metrics = cognitif_calculer_score()

    conn = sqlite3.connect(DB_PATH)

    # Score history
    history = conn.execute(
        "SELECT date, score_global FROM intelligence_score ORDER BY date DESC LIMIT 7"
    ).fetchall()

    hypotheses = conn.execute(
        "SELECT statement, confidence, predictions, confirmations FROM hypotheses "
        "WHERE active=1 ORDER BY confidence DESC LIMIT 5"
    ).fetchall()

    # Top expertise
    top_exp = conn.execute(
        "SELECT insight, confidence FROM expertise ORDER BY confidence DESC LIMIT 5"
    ).fetchall()

    conn.close()

    s = metrics
    report = (
        f"🧠 INTELLIGENCE — {s['level']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"📊 SCORE GLOBAL : {s['score']}/100\n"
        f"\n"
        f"  Expertise: {s['expertise']} rules → {min(25, s['expertise'] * 0.5):.0f}/25 pts\n"
        f"  Prediction: {s['prediction_rate']:.0f}% correct → {min(25, s['prediction_rate'] * 0.25):.0f}/25 pts\n"
        f"  Coverage   : {s['coverage']:.0f}% baselines → {min(25, s['coverage'] * 0.25):.0f}/25 pts\n"
        f"  Resilience: {s['success_day']}✅ {s['failures_day']}❌ → {s.get('resilience_pts', 0):.0f}/25 pts\n"
    )

    # Barre of progression
    filled = int(s['score'] / 10)
    bar = "█" * filled + "░" * (10 - filled)
    report += f"\n  [{bar}] {s['score']:.0f}%\n"

    if len(history) >= 2:
        prev = history[1][1]
        delta = s['score'] - prev
        arrow = "↗️" if delta > 0 else ("↘️" if delta < 0 else "→")
        report += f"\n📈 Evolution : {arrow} {delta:+.1f} pts since hier\n"

    if len(history) >= 2:
        report += "\n📅 HISTORY :\n"
        for date, score in history[:7]:
            bar_h = "█" * int(score / 10) + "░" * (10 - int(score / 10))
            report += f"  {date[5:10]} [{bar_h}] {score:.0f}\n"

    if hypotheses:
        report += f"\n🔮 ACTIVE HYPOTHESES ({s['hypotheses']}) :\n"
        for statement, conf, pred, confirm in hypotheses:
            rate = f"{confirm}/{pred}" if pred > 0 else "not tested"
            stars = "★" * min(5, int(conf * 5))
            report += f"  {stars} {statement[:60]}\n    → {rate}\n"

    # Top expertise
    if top_exp:
        report += f"\n📚 TOP EXPERTISE :\n"
        for insight, conf in top_exp:
            stars = "★" * min(5, int(conf * 5))
            report += f"  {stars} {insight[:70]}\n"

    # Savings
    if s['saving'] > 0:
        report += f"\n💰 ESTIMATED SAVINGS: {s['saving']:.2f} (solar cycles)\n"

    report += f"\n🧠 {s['skills']} skills | {s['baselines']} baselines | {s['hypotheses']} hypotheses"

    return report


def learning_log_failure(source, description, context=None):
    """Record a failure to derive a lesson"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO decisions_log (action, context, result, success, created_at) VALUES (?, ?, ?, 0, ?)",
        (f"FAILURE_{source}", json.dumps(context or {}, ensure_ascii=False),
         description, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    log.warning(f"📕 Failure logged [{source}]: {description[:100]}")


def learning_log_success(source, description, context=None):
    """Record a success for renforcer the pattern"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO decisions_log (action, context, result, success, created_at) VALUES (?, ?, ?, 1, ?)",
        (f"SUCCES_{source}", json.dumps(context or {}, ensure_ascii=False),
         description, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def learning_tirer_lessons():
    """Analyze recent failures and derive lessons every 12h.

    This is where the AI learns from failures and creates reusable rules.
    """

    conn = sqlite3.connect(DB_PATH)

    sept_days = (datetime.now() - timedelta(days=7)).isoformat()
    failures = conn.execute(
        "SELECT action, context, result, created_at FROM decisions_log "
        "WHERE success=0 AND created_at > ? ORDER BY created_at DESC LIMIT 20",
        (sept_days,)
    ).fetchall()

    success = conn.execute(
        "SELECT action, result FROM decisions_log "
        "WHERE success=1 AND created_at > ? ORDER BY created_at DESC LIMIT 20",
        (sept_days,)
    ).fetchall()

    # Load existing expertise
    expertise = conn.execute(
        "SELECT insight FROM expertise ORDER BY confidence DESC LIMIT 15"
    ).fetchall()

    conn.close()

    if not failures and not success:
        return  # Nothing to learn

    if not check_budget():
        return

    # Build the prompt
    data = []
    if failures:
        data.append(f"RECENT FAILURES ({len(failures)}) :")
        for action, ctx, res, date in failures:
            data.append(f"  [{date[:16]}] {action}: {res[:150]}")

    if success:
        data.append(f"\nRECENT SUCCESSES ({len(success)}) :")
        for action, res in success[:10]:
            data.append(f"  {action}: {res[:100]}")

    if expertise:
        data.append("\nCURRENT EXPERTISE :")
        for (ins,) in expertise:
            data.append(f"  {ins}")

    prompt_system = (
        "You are an AI that learns from errors. Analyze failures and successes "
        "to extract PERMANENT LESSONS.\n\n"
        "OBJECTIVE: Each lesson is a rule your should never violate again.\n\n"
        "RESPOND WITH STRICT JSON :\n"
        "{\n"
        "  \"lessons\": [\n"
        "    {\"category\": \"monitoring|alert|code|energy|zigbee|general\",\n"
        "     \"lesson\": \"the rule to retain (imperative, max 80 chars)\",\n"
        "     \"source_failure\": \"which failure taught this\",\n"
        "     \"confidence\": 0.6}\n"
        "  ],\n"
        "  \"patterns_success\": [\"what worked and should be repeated\"],\n"
        "  \"summary\": \"one-sentence summary\"\n"
        "}\n"
        "No markdown, only JSON. If there is nothing to learn : {\"lessons\": [], \"patterns_success\": [], \"summary\": \"RAS\"}"
    )

    try:
        blocks, t_in, t_out = llm_provider.llm_completion(
            CFG, [{"role": "user", "content": "\n".join(data)}],
            max_tokens=600,
            system_prompt=prompt_system
        )
        text = llm_provider.stream_text(blocks).strip().replace("```json", "").replace("```", "").strip()
        log_token_usage(t_in, t_out)

        try:
            result = json.loads(text)
        except Exception:
            return

        lessons = result.get("lessons", [])
        patterns = result.get("patterns_success", [])
        summary = result.get("summary", "")

        conn2 = sqlite3.connect(DB_PATH)
        new_items = 0
        for lesson in lessons:
            text_l = lesson.get("lesson", "")
            cat = lesson.get("category", "general")
            conf = lesson.get("confidence", 0.6)
            source = lesson.get("source_failure", "")
            if not text_l or len(text_l) < 10:
                continue

            existing = conn2.execute(
                "SELECT id, confidence FROM expertise WHERE category=? AND insight LIKE ?",
                (cat, f"%{text_l[:20]}%",)
            ).fetchone()

            if existing:
                new_conf = min(1.0, existing[1] + 0.15)
                conn2.execute(
                    "UPDATE expertise SET confidence=?, nb_validations=nb_validations+1, updated_at=? WHERE id=?",
                    (new_conf, datetime.now().isoformat(), existing[0])
                )
            else:
                nb_total = conn2.execute("SELECT COUNT(*) FROM expertise").fetchone()[0]
                if nb_total >= 50:
                    conn2.execute(
                        "DELETE FROM expertise WHERE id = ("
                        "SELECT id FROM expertise WHERE source NOT LIKE 'founding_lesson%' "
                        "ORDER BY confidence ASC LIMIT 1)")
                conn2.execute(
                    "INSERT INTO expertise (category, insight, confidence, nb_validations, source, created_at, updated_at) "
                    "VALUES (?, ?, ?, 1, ?, ?, ?)",
                    (cat, text_l, conf, f"failure:{source}", datetime.now().isoformat(), datetime.now().isoformat())
                )
                new_items += 1

        conn2.commit()
        conn2.close()

        if new_items > 0 or lessons:
            telegram_send(
                f"📕 LEARNING\n━━━━━━━━━━━━━━━━━━\n"
                f"{summary}\n\n"
                f"New lessons: {new_items}\n"
                f"Lessons reinforced: {len(lessons) - new_items}\n"
                f"Success patterns: {len(patterns)}"
            )
            log.info(f"📕 Learning: {new_items} new lessons, {len(lessons)} total")

    except Exception as e:
        log.error(f"❌ learning_tirer_lessons: {e}")


def learning_auto_correction():
    """Checks whether recurring problems require an automatic patch.
    Called every 24h. If the same failure type appears more than 3 times, proposes a fix."""

    conn = sqlite3.connect(DB_PATH)

    sept_days = (datetime.now() - timedelta(days=7)).isoformat()
    failures = conn.execute(
        "SELECT action, COUNT(*) as nb FROM decisions_log "
        "WHERE success=0 AND created_at > ? GROUP BY action HAVING nb >= 3 "
        "ORDER BY nb DESC LIMIT 5",
        (sept_days,)
    ).fetchall()

    conn.close()

    if not failures:
        return

    if not check_budget():
        return

    for action, nb in failures:
        log.debug(f"Recurring failure: {action} ({nb}x/7j)")


def baseline_collect(states):
    """Update hourly baselines for key numeric Home Assistant sensors."""
    try:
        if not states:
            return
        _refresh_baseline_entities()
        index = {e.get("entity_id"): e for e in states}
        now = datetime.now()
        weekday = now.weekday()
        hour = now.hour

        conn = sqlite3.connect(DB_PATH)
        for entity_id in list(BASELINE_ENTITIES.keys()):
            entity = index.get(entity_id)
            if not entity or entity.get("state") in ("unavailable", "unknown", None):
                continue
            value = _parse_numeric_state(entity.get("state"))
            if value is None:
                continue

            row = conn.execute(
                "SELECT avg_value, sample_count FROM baselines WHERE entity_id=? AND weekday=? AND hour=?",
                (entity_id, weekday, hour),
            ).fetchone()
            if row:
                avg_value, sample_count = row
                sample_count = int(sample_count or 0) + 1
                avg_value = ((float(avg_value or 0) * (sample_count - 1)) + value) / sample_count
                conn.execute(
                    "UPDATE baselines SET avg_value=?, sample_count=?, updated_at=? "
                    "WHERE entity_id=? AND weekday=? AND hour=?",
                    (avg_value, sample_count, now.isoformat(), entity_id, weekday, hour),
                )
            else:
                conn.execute(
                    "INSERT INTO baselines (entity_id, weekday, hour, avg_value, sample_count, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (entity_id, weekday, hour, value, 1, now.isoformat()),
                )
        conn.commit()
        conn.close()
    except Exception as ex:
        log.debug(f"baseline_collect: {ex}")


def baseline_detect_anomalies(states):
    """Best-effort baseline comparison hook; intentionally quiet for beta installs."""
    try:
        if not states:
            return []
        index = {e.get("entity_id"): e for e in states}
        now = datetime.now()
        weekday = now.weekday()
        hour = now.hour
        anomalies = []

        conn = sqlite3.connect(DB_PATH)
        for entity_id, label in BASELINE_ENTITIES.items():
            entity = index.get(entity_id)
            if not entity or entity.get("state") in ("unavailable", "unknown", None):
                continue
            value = _parse_numeric_state(entity.get("state"))
            if value is None:
                continue
            row = conn.execute(
                "SELECT avg_value, sample_count FROM baselines WHERE entity_id=? AND weekday=? AND hour=?",
                (entity_id, weekday, hour),
            ).fetchone()
            if not row or int(row[1] or 0) < 10:
                continue
            avg_value = float(row[0] or 0)
            if avg_value <= 0:
                continue
            deviation = abs(value - avg_value) / avg_value * 100
            if deviation >= 300:
                anomalies.append({
                    "entity_id": entity_id,
                    "label": label,
                    "current": value,
                    "average": avg_value,
                    "deviation_pct": round(deviation),
                })
        conn.close()
        return anomalies
    except Exception as ex:
        log.debug(f"baseline_detect_anomalies: {ex}")
        return []


def _watch_condition_met(entity, condition, state_value):
    state = str(entity.get("state", "")).lower()
    condition = str(condition or "").lower()
    target = str(state_value or "").lower()
    if condition in ("unavailable", "offline"):
        return state in ("unavailable", "unknown", "offline")
    if condition == "equals":
        return state == target
    if condition == "not_equals":
        return state != target
    if condition in ("above", "below"):
        try:
            value = float(str(entity.get("state")).replace(",", "."))
            threshold = float(str(state_value).replace(",", "."))
            return value > threshold if condition == "above" else value < threshold
        except Exception:
            return False
    return False


def _check_dynamic_watches(index, now):
    """Evaluate user-created watches against live Home Assistant state."""
    try:
        import fnmatch
        conn = sqlite3.connect(DB_PATH)
        watches = conn.execute(
            "SELECT id, entity_pattern, condition, state_value, message, cooldown_min, last_triggered "
            "FROM watches WHERE active=1"
        ).fetchall()
        for watch_id, pattern, condition, state_value, message, cooldown, last_triggered in watches:
            matched = [
                (eid, entity) for eid, entity in index.items()
                if eid == pattern or fnmatch.fnmatch(eid, pattern)
            ]
            for eid, entity in matched:
                triggered = False
                condition_key = str(condition or "").lower()
                if condition_key == "open_for":
                    open_states = _ha_open_states_for_entity(eid)
                    open_now = str(entity.get("state", "")).lower() in open_states
                    mem_key = f"watch_open_since_{watch_id}_{eid}"
                    if open_now:
                        opened_at = mem_get(mem_key)
                        if not opened_at:
                            mem_set(mem_key, now.isoformat())
                        else:
                            try:
                                minutes = int(float(state_value or 10))
                                opened_dt = datetime.fromisoformat(opened_at[:19])
                                triggered = (now - opened_dt).total_seconds() >= minutes * 60
                            except Exception:
                                triggered = False
                    else:
                        mem_set(mem_key, "")
                else:
                    triggered = _watch_condition_met(entity, condition, state_value)

                if not triggered:
                    continue

                if last_triggered:
                    try:
                        last_dt = datetime.fromisoformat(last_triggered[:19])
                        if (now - last_dt).total_seconds() < int(cooldown or 60) * 60:
                            continue
                    except Exception:
                        pass

                friendly = _ha_entity_label(entity)
                alert = (message or "{friendly_name} matched watch condition").format(
                    entity_id=eid,
                    state=entity.get("state", "?"),
                    friendly_name=friendly,
                )
                telegram_send(f"🔔 {alert}")
                conn.execute(
                    "UPDATE watches SET last_triggered=? WHERE id=?",
                    (now.isoformat(), watch_id),
                )
                last_triggered = now.isoformat()
        conn.commit()
        conn.close()
    except Exception as ex:
        log.debug(f"Dynamic watches: {ex}")


def _cycle_intelligence(states, index, now):
    """Autonomous brain - runs every 5 minutes"""
    # global _intelligence_counter  # via shared
    shared._intelligence_counter += 1

    snapshot = _observer(states, index, now)

    baseline_collect(states)

    skill_window_solar(states)
    skill_heat_pump_behavior(states)
    skill_optimisation_rate(states)
    try:
        _rate_learn_off_peak_ranges(states)
    except Exception:
        pass
    skill_dynamic_collect(states)

    if _intelligence_counter % 3 == 0:
        try:
            skill_health_host()
        except Exception as ex_sh:
            log.error(f"❌ health_host: {ex_sh}")

    anomalies = _compare(states, index, now, snapshot)

    _decider(anomalies, states, index, now)

    skill_suggestion_machine(states)

    if _intelligence_counter % 12 == 0:  # Every hour
        _auto_learn(states, index, now)

    try:
        test_cognitive_hypotheses(states, index)
        if _intelligence_counter % 12 == 0:  # Every hour
            generate_cognitive_hypotheses(states, index)
        if _intelligence_counter % 288 == 0:  # Every the 24h
            score_data = cognitif_calculer_score()
            if score_data["score"] > 0:
                log.info(f"🧠 Score intelligence: {score_data['score']}/100 ({score_data['level']})")
    except Exception as ex_cog:
        log.error(f"❌ cognitif: {ex_cog}")

    if _intelligence_counter % 72 == 0 and _intelligence_counter > 0:
        try:
            _analysis_ia_periodique(states, index, now)
        except Exception as ex_ia:
            log.error(f"❌ analysis_ia: {ex_ia}")
            learning_log_failure("analysis_ia", str(ex_ia))

    if _intelligence_counter % 144 == 0 and _intelligence_counter > 0:
        try:
            learning_tirer_lessons()
        except Exception as ex_app:
            log.error(f"❌ learning: {ex_app}")

    if _intelligence_counter % 144 == 0 and _intelligence_counter > 0:
        try:
            filter_analyze_messages()
        except Exception as ex_fa:
            log.error(f"❌ filter_analyze: {ex_fa}")

    if _intelligence_counter % 288 == 0 and _intelligence_counter > 0:
        try:
            learning_auto_correction()
        except Exception as ex_ctrl:
            log.error(f"❌ auto_correction: {ex_ctrl}")

    if _intelligence_counter % 288 == 72 and _intelligence_counter > 72:
        try:
            skill_proactive_recommendations()
        except Exception as ex_recommendation:
            log.error(f"❌ recommendations: {ex_recommendation}")

    # Log discret
    if _intelligence_counter % 60 == 0:  # Every the 5h
        skill_count = 0
        try:
            conn = sqlite3.connect(DB_PATH)
            skill_count = conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
            baseline_count = conn.execute("SELECT COUNT(*) FROM baselines").fetchone()[0]
            conn.close()
        except Exception:
            pass
        log.info(f"🧠 Intelligence: cycle #{_intelligence_counter} | {skill_count} skills | {baseline_count} baselines")


def _observer(states, index, now):
    """Phase 1: Captures a structured snapshot of the home state"""
    snapshot = {
        "timestamp": now.isoformat(),
        "nb_entities": len(states),
        "nb_unavailable": sum(1 for e in states if e["state"] in ("unavailable", "unknown")),
    }

    # Production solar
    try:
        snapshot["production_w"] = ha_get_current_solar_production(states)
    except Exception:
        snapshot["production_w"] = 0

    # Grid consumption
    e_eco = index.get(role_get("realtime_consumption") or "sensor.ecojoko_realtime_consumption")
    if e_eco and e_eco["state"] not in ("unavailable", "unknown"):
        try:
            snapshot["grid_consumption_w"] = float(e_eco["state"])
        except Exception:
            pass

    for eid, key in [
        (role_get("indoor_temperature") or "sensor.ecojoko_indoor_temperature", "temp_int"),
        (role_get("outdoor_temperature") or "sensor.ecojoko_outdoor_temperature", "temp_ext"),
    ]:
        e = index.get(eid)
        if e and e["state"] not in ("unavailable", "unknown"):
            try:
                snapshot[key] = float(e["state"])
            except Exception:
                pass

    # heat pump
    for e in states:
        if e["entity_id"].startswith("climate."):
            carto = entity_map_get(e["entity_id"])
            if carto and "heating" in carto[0]:
                snapshot["heat_pump_state"] = e["state"]
                snapshot["heat_pump_on"] = e["state"] in ("auto", "heat", "cool", "fan_only", "heat_cool")
                break

    # Active plugs
    snapshot["active_plugs"] = sum(1 for eid, state in _state_plugs.items() if state == "active")

    mem_set("last_snapshot", json.dumps(snapshot))

    return snapshot


def _compare(states, index, now, snapshot):
    """Phase 4: Compares current state with memory and baselines"""
    anomalies = []

    # Baseline anomalies
    baseline_detect_anomalies(states)

    # Anomaly: abnormally high number of unavailable entities
    pct_ko = snapshot["nb_unavailable"] / max(snapshot["nb_entities"], 1) * 100
    if pct_ko > 30:
        anomalies.append({
            "type": "entities_ko",
            "message": f"{snapshot['nb_unavailable']}/{snapshot['nb_entities']} entities offline ({pct_ko:.0f}%)",
            "severity": "high" if pct_ko > 50 else "medium",
        })

    # Anomaly: solar production is zero in full daylight
    if ha_is_day(states) and snapshot.get("production_w", 0) == 0:
        hour = now.hour
        if 9 <= hour <= 16:
            prev_zero = False
            try:
                prev_json = mem_get("previous_snapshot")
                if prev_json:
                    prev = json.loads(prev_json)
                    if prev.get("production_w", 0) == 0:
                        prev_zero = True
            except Exception:
                pass
            if prev_zero:
                anomalies.append({
                    "type": "solar_zero",
                    "message": f"Solar production 0W confirmed in daylight ({hour}h) — 2 consecutive cycles",
                    "severity": "high",
                })

    # Anomaly: grid consumption is abnormally high
    consumption = snapshot.get("grid_consumption_w", 0)
    if consumption > 8000:
        anomalies.append({
            "type": "consumption_extreme",
            "message": f"Very high grid consumption: {int(consumption)}W",
            "severity": "high",
        })

    temp_int = snapshot.get("temp_int")
    if temp_int is not None and temp_int < 16:
        anomalies.append({
            "type": "temp_basse",
            "message": f"Low indoor temperature: {temp_int}°C",
            "severity": "high" if temp_int < 14 else "medium",
        })



    # Save for the next cycle
    mem_set("previous_snapshot", json.dumps(snapshot))

    return anomalies


def _decider(anomalies, states, index, now):
    """Phase 5 : Acts on the detected anomalies"""
    for anomaly in anomalies:
        severity = anomaly.get("severity", "medium")
        msg = anomaly["message"]
        atype = anomaly["type"]

        if severity == "high":
            _alert_if_new(
                f"ia_{atype}",
                f"🧠 ALERTE INTELLIGENCE\n🚨 {msg}",
                delay_h=2
            )
        elif severity == "medium":
            _alert_if_new(
                f"ia_{atype}",
                f"🧠 INTELLIGENCE\n⚠️ {msg}",
                delay_h=6
            )


def _auto_learn(states, index, now):
    """Phase 7 : Analysis the patterns recurring and cree of new_items competences"""
    conn = sqlite3.connect(DB_PATH)

    categories_energy = ["energy_solar", "energy_consumption", "energy_heating",
                          "connected_plug", "energy_battery", "energy_production"]

    entities_suivies = set(BASELINE_ENTITIES.keys())
    nb_dyn = conn.execute("SELECT COUNT(*) FROM skills WHERE name LIKE 'dyn_%'").fetchone()[0]

    if nb_dyn >= 10:
        conn.close()
        return  # Deja at the max

    for cat in categories_energy:
        entities_cat = entity_map_get_by_category(cat)
        for eid, sc, pc in entities_cat:
            if eid in entities_suivies:
                continue
            if not eid.startswith("sensor."):
                continue

            e = index.get(eid)
            if not e or e["state"] in ("unavailable", "unknown"):
                continue

            unit = e.get("attributes", {}).get("unit_of_measurement", "")
            if unit not in ("W", "kWh", "°C", "%"):
                continue

            existing = conn.execute(
                "SELECT name FROM skills WHERE name LIKE 'dyn_%' AND data LIKE ?",
                (f"%{eid}%",)
            ).fetchone()
            if existing:
                continue

            fname = e.get("attributes", {}).get("friendly_name", eid)
            definition = {
                "description": f"Suivi auto {fname} ({unit})",
                "entities": [eid],
                "action": "collect",
                "threshold": None,
                "cree_le": now.isoformat(),
                "created_by": "auto_learn",
                "history": []
            }
            name = f"dyn_auto_{eid.split('.')[1][:30]}"
            skill_set(name, definition, 0)
            log.info(f"🧠 Auto-learning : {name} — {fname}")

            nb_dyn += 1
            if nb_dyn >= 10:
                break
        if nb_dyn >= 10:
            break

    conn.close()


def _analysis_ia_periodique(states, index, now):
    """Phase 8: the configured AI analyzes accumulated data and produces insights.
    This is where the AI provides value through correlation, prediction, and optimization.
    A human cannot do this continuously for free."""

    if not check_budget():
        return  # No tokens → no analysis

    conn = sqlite3.connect(DB_PATH)

    ai_data = []

    # 1. Baselines : patterns by day/hour
    baselines_summary = {}
    rows = conn.execute(
        "SELECT entity_id, weekday, hour, avg_value, sample_count FROM baselines WHERE sample_count >= 5 ORDER BY entity_id, weekday, hour"
    ).fetchall()
    for eid, day, hour, avg, nb in rows:
        label = BASELINE_ENTITIES.get(eid, eid)
        if label not in baselines_summary:
            baselines_summary[label] = {}
        days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        key_name = f"{days[day]}_{hour}h"
        baselines_summary[label][key_name] = round(avg, 1)

    if baselines_summary:
        ai_data.append("BEHAVIOR BASELINES (averages by slot) :")
        for label, slotx in baselines_summary.items():
            vals = list(slotx.values())
            if vals:
                pic_slot = max(slotx.items(), key=lambda x: x[1])
                low_slot = min(slotx.items(), key=lambda x: x[1])
                ai_data.append(
                    f"  {label}: pic={pic_slot[0]}→{pic_slot[1]} | "
                    f"creux={low_slot[0]}→{low_slot[1]} | "
                    f"medium={sum(vals)/len(vals):.0f} ({len(slotx)} slotx)"
                )

    # 2. Skills : what the AI learned
    skills_rows = conn.execute("SELECT name, data, nb_learning samples FROM skills").fetchall()
    for name, data_json, nb in skills_rows:
        try:
            data = json.loads(data_json)
            if name == "window_solar" and nb >= 10:
                days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
                pics = []
                for j in range(7):
                    j_str = str(j)
                    if j_str in data:
                        best = max(data[j_str].items(), key=lambda x: x[1][0])
                        pics.append(f"{days[j]} {best[0]}h={int(best[1][0])}W")
                if pics:
                    ai_data.append(f"SKILL SOLAR WINDOW ({nb} learning samples) : " + " | ".join(pics))

            elif name == "cycle_signatures" and data:
                for eid, info in data.items():
                    ai_data.append(
                        f"SKILL MACHINE {info['name']}: {info['duration_avg']:.0f}min, "
                        f"{info['consumption_avg']:.2f}kWh, {info['power_avg']:.0f}W, "
                        f"{info['nb_cycles']} cycles"
                    )

            elif name == "heat_pump_behavior" and nb >= 10:
                tranches = data.get("tranches", {})
                heat_pump_summary = []
                for temp in sorted(tranches.keys(), key=lambda x: float(x)):
                    t = tranches[temp]
                    total = t["heat_pump_on"] + t["heat_pump_off"]
                    if total >= 5:
                        pct = int(t["heat_pump_on"] / total * 100)
                        heat_pump_summary.append(f"{temp}°C→heat pump:{pct}%on/{t['consumption_avg']:.0f}W")
                if heat_pump_summary:
                    ai_data.append(f"SKILL heat pump ({nb} obs) : " + " | ".join(heat_pump_summary))

            elif name.startswith("dyn_") and "history" in data and len(data["history"]) >= 5:
                desc = data.get("description", name)
                hist = data["history"]
                vals_num = []
                for h in hist[-20:]:
                    v = h.get("value_texts", {})
                    for val in v.values():
                        if isinstance(val, (int, float)):
                            vals_num.append(val)
                if vals_num:
                    ai_data.append(
                        f"SKILL DYN {desc}: avg={sum(vals_num)/len(vals_num):.1f} "
                        f"min={min(vals_num):.1f} max={max(vals_num):.1f} ({len(hist)} points)"
                    )
        except Exception:
            pass

    # 3. Recent appliance cycles
    cycles = conn.execute(
        "SELECT friendly_name, started_at, duration_min, consumption_kwh, cost_eur, solar_production_w "
        "FROM appliance_cycles WHERE ended_at IS NOT NULL ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    if cycles:
        ai_data.append("HISTORY CYCLES (last 10) :")
        total_kwh = 0
        total_cost = 0
        for fname, started_at, duration, consumption, cost, prod in cycles:
            date = started_at[:10] if started_at else "?"
            solar = f" | solar:{prod}W" if prod else ""
            ai_data.append(f"  {fname} {date} {duration}min {consumption:.2f}kWh {cost:.2f}€{solar}")
            total_kwh += consumption or 0
            total_cost += cost or 0
        ai_data.append(f"  TOTAL: {total_kwh:.2f}kWh | {total_cost:.2f}€")

    snapshot_json = mem_get("last_snapshot", "{}")
    try:
        snapshot = json.loads(snapshot_json)
        ai_data.append(f"CURRENT SNAPSHOT: prod={snapshot.get('production_w', 0)}W | "
                         f"grid_consumption={snapshot.get('grid_consumption_w', 0)}W | "
                         f"temp_int={snapshot.get('temp_int', '?')}°C | "
                         f"temp_ext={snapshot.get('temp_ext', '?')}°C | "
                         f"heat pump={'ON' if snapshot.get('heat_pump_on') else 'OFF'} | "
                         f"active_plugs={snapshot.get('active_plugs', 0)}")
    except Exception:
        pass

    conn.close()

    if len(ai_data) < 3:
        return  # Not enough data for a useful analysis

    existing_expertise = []
    try:
        rows_exp = conn.execute(
            "SELECT category, insight, confidence, nb_validations FROM expertise "
            "ORDER BY confidence DESC LIMIT 20"
        ).fetchall()
        for cat, insight, conf, nb_val in rows_exp:
            stars = "★" * min(5, int(conf * 5))
            existing_expertise.append(f"[{cat}] {stars} ({nb_val} valid.) : {insight}")
    except Exception:
        pass

    conn.close()

    if len(ai_data) < 3:
        return

    prompt_system = (
        "You are the user's lead home automation and energy expert. "
        "You build expertise with each analysis — every token spent makes your smarter.\n\n"
        "You have access to:\n"
        "1. Raw home data (baselines, skills, cycles, snapshot)\n"
        "2. Accumulated expertise from previous analyses\n\n"
        "Your role:\n"
        "- Correlate: find hidden relationships in the data\n"
        "- Predict: anticipate problems and opportunities\n"
        "- Optimize: propose concrete actions that save energy or cost\n"
        "- Capture: extract reusable rules\n\n"
        "RESPOND WITH STRICT JSON :\n"
        "{\n"
        "  \"analysis\": \"concise analysis (max 400 chars)\",\n"
        "  \"insights\": [\n"
        "    {\"category\": \"energy|heat_pump|solar|machine|zigbee|general\",\n"
        "     \"insight\": \"the applied rule or correlation (max 100 chars)\",\n"
        "     \"confidence\": 0.5}\n"
        "  ],\n"
        "  \"recommended_actions\": [\"action 1\", \"action 2\"],\n"
        "  \"expertise_obsolete\": [\"insight obsolete insight to remove if found\"]\n"
        "}\n"
        "No markdown, no ```, only the JSON.\n"
        "If there is not enough data : {\"analysis\": \"Insufficient data\", \"insights\": [], \"recommended_actions\": [], \"expertise_obsolete\": []}"
    )

    prompt_user = "ACCUMULATED DATA :\n" + "\n".join(ai_data)
    if existing_expertise:
        prompt_user += "\n\nACCUMULATED EXPERTISE (build on it, do not repeat it) :\n" + "\n".join(existing_expertise)

    try:
        blocks, t_in, t_out = llm_provider.llm_completion(
            CFG, [{"role": "user", "content": prompt_user}],
            max_tokens=1200,
            system_prompt=prompt_system
        )
        text = llm_provider.stream_text(blocks).strip().replace("```json", "").replace("```", "").strip()
        log_token_usage(t_in, t_out)

        try:
            result = json.loads(text)
        except Exception:
            result = {"analysis": text[:400], "insights": [], "recommended_actions": [], "expertise_obsolete": []}

        analysis = result.get("analysis", "")
        insights = result.get("insights", [])
        actions = result.get("recommended_actions", [])
        obsoletes = result.get("expertise_obsolete", [])

        conn2 = sqlite3.connect(DB_PATH)
        for ins in insights:
            cat = ins.get("category", "general")
            text_ins = ins.get("insight", "")
            conf = ins.get("confidence", 0.5)
            if not text_ins or len(text_ins) < 10:
                continue

            existing = conn2.execute(
                "SELECT id, confidence, nb_validations FROM expertise WHERE category=? AND insight LIKE ?",
                (cat, f"%{text_ins[:20]}%",)
            ).fetchone()

            if existing:
                new_conf = min(1.0, existing[1] + 0.1)
                conn2.execute(
                    "UPDATE expertise SET confidence=?, nb_validations=nb_validations+1, updated_at=? WHERE id=?",
                    (new_conf, now.isoformat(), existing[0])
                )
            else:
                nb_total = conn2.execute("SELECT COUNT(*) FROM expertise").fetchone()[0]
                if nb_total >= 50:
                    # Remove the weakest non-foundational insight
                    conn2.execute(
                        "DELETE FROM expertise WHERE id = ("
                        "SELECT id FROM expertise WHERE source NOT LIKE 'founding_lesson%' "
                        "ORDER BY confidence ASC LIMIT 1)"
                    )
                conn2.execute(
                    "INSERT INTO expertise (category, insight, confidence, nb_validations, source, created_at, updated_at) "
                    "VALUES (?, ?, ?, 1, 'analysis_auto', ?, ?)",
                    (cat, text_ins, conf, now.isoformat(), now.isoformat())
                )
                log.info(f"🧠 Norvel insight: [{cat}] {text_ins[:60]}...")

        # Remove obsolete expertise
        for obs in obsoletes:
            if obs and len(obs) > 10:
                conn2.execute(
                    "DELETE FROM expertise WHERE insight LIKE ? AND confidence < 0.5",
                    (f"%{obs[:50]}%",)
                )

        conn2.execute(
            "INSERT INTO decisions_log (action, context, result, created_at) VALUES (?, ?, ?, ?)",
            ("periodic_analysis", json.dumps({"nb_data": len(ai_data)}, ensure_ascii=False),
             json.dumps({"nb_insights": len(insights), "nb_actions": len(actions)}, ensure_ascii=False),
             now.isoformat())
        )

        conn2.commit()
        conn2.close()

        # Store the analysis
        mem_set("last_analysis_ia", analysis)
        mem_set("last_analysis_ia_date", now.isoformat())

        # Final analysis: remove any residual JSON
        analysis_clean = analysis
        if "{" in analysis_clean or '"analysis"' in analysis_clean:
            # Attempt 1 : parse as full JSON
            try:
                parsed = json.loads(analysis_clean)
                if isinstance(parsed, dict) and "analysis" in parsed:
                    analysis_clean = parsed["analysis"]
            except Exception:
                import re
                m = re.search(r'"analysis"\s*:\s*"((?:[^"\\]|\\.)*)"', analysis_clean)
                if m:
                    analysis_clean = m.group(1).replace('\\"', '"').replace('\\n', ' ')
                else:
                    analysis_clean = re.sub(r'[{\[\]}":]', '', analysis_clean)
                    analysis_clean = re.sub(r'\s+', ' ', analysis_clean).strip()[:500]

        msg = f"🧠 ANALYSIS INTELLIGENCE\n{now.strftime('%d/%m %H:%M')}\n━━━━━━━━━━━━━━━━━━\n{analysis_clean}"
        if actions:
            msg += "\n\n💡 RECOMMANDATIONS :\n" + "\n".join(f"  • {a}" for a in actions[:3])
        nb_exp = 0
        try:
            conn3 = sqlite3.connect(DB_PATH)
            nb_exp = conn3.execute("SELECT COUNT(*) FROM expertise").fetchone()[0]
            conn3.close()
        except Exception:
            pass
        msg += f"\n\n📚 Expertise: {nb_exp} rules learned"

        telegram_send(msg)
        log.info(f"🧠 Analysis AI: {len(insights)} insights, {len(actions)} actions ({r.usage.input_tokens}+{r.usage.output_tokens} tokens)")

    except Exception as e:
        log.error(f"❌ analysis_ia: {e}")


def rate_cost_cycle(consumption_kwh, duration_min, started_at_iso=None):
    """Calculate the full cycle cost, accounting for peak/off-peak overlap."""
    rate = rate_get()

    if rate.get("type") == "base":
        price = rate.get("price_kwh", 0.2516)
        return {
            "cost_total": round(consumption_kwh * price, 3),
            "detail": f"{price}/kWh (base)",
            "price_avg_kwh": price
        }

    if rate.get("type") == "hphc":
        price_started_at = rate_current_kwh_price()
        if started_at_iso:
            try:
                started_at_dt = datetime.fromisoformat(started_at_iso)
                h_started_at = started_at_dt.hour * 60 + started_at_dt.minute
                off_peak_hours = rate.get("off_peak_hours", [])
                started_at_hc = False
                for range in off_peak_hours:
                    d_str, f_str = range.split("-")
                    dh, dm = map(int, d_str.split(":"))
                    fh, fm = map(int, f_str.split(":"))
                    d_min, f_min = dh * 60 + dm, fh * 60 + fm
                    if d_min > f_min:
                        if h_started_at >= d_min or h_started_at < f_min:
                            started_at_hc = True
                    else:
                        if d_min <= h_started_at < f_min:
                            started_at_hc = True
                price_started_at = rate.get("price_hc" if started_at_hc else "price_hp", 0.25)
            except Exception:
                pass

        price_ended_at = rate_current_kwh_price()
        price_avg = (price_started_at + price_ended_at) / 2
        cost = round(consumption_kwh * price_avg, 3)
        detail_hp = f"peak:{rate.get('price_hp')}" if not rate_is_off_peak_hour() else ""
        detail_hc = f"off-peak:{rate.get('price_hc')}" if rate_is_off_peak_hour() else ""
        return {
            "cost_total": cost,
            "detail": f"{price_avg:.4f}/kWh ({detail_hp or detail_hc})",
            "price_avg_kwh": price_avg
        }

    return {"cost_total": round(consumption_kwh * 0.2516, 3), "detail": "default", "price_avg_kwh": 0.2516}


def _rate_detect_off_peak_hours():
    """Automatically detects the off-peak hours in HA.
    Supports Home Assistant rate sensors and peak/off-peak indexes.
    If ranges cannot be found directly, enable auto-learning."""
    states = ha_get("states")
    if not states:
        return None

    off_peak_confirmed = False  # The contract has peak/off-peak pricing, not a single base rate.
    ptec_entity = None    # Current rate period entity

    for e in states:
        eid = e["entity_id"]
        eid_low = eid.lower()
        attrs = e.get("attributes", {})
        state = e["state"]

        # ═══ 1. Attribute direct attribute with hour ranges ═══
        for k, v in attrs.items():
            k_low = str(k).lower()
            if any(kw in k_low for kw in ["off_peak", "off_peak", "offpeak_hours",
                                           "hc_hours", "off_peak_hours_ranges"]):
                if isinstance(v, str) and "-" in v:
                    return [p.strip() for p in v.split(",") if "-" in p]
                if isinstance(v, list):
                    return v

        # PTEC = "HC.." (off-peak hours) or "HP.." (peak hours)
        if any(kw in eid_low for kw in ["ptec", "current_rate", "periode_rate",
                                         "current_rate", "rate_index"]):
            ptec_entity = eid
            if "hc" in state.lower():
                off_peak_confirmed = True

        # ═══ 3. Cumulative peak/off-peak indexes confirm the subscription ═══
        if any(kw in eid_low for kw in ["hchc", "hchp", "index_hc", "index_hp",
                                         "consumption_hc", "consumption_hp",
                                         "off_peak_hours_index", "peak_hours_index"]):
            off_peak_confirmed = True

        if "ecojoko" in eid_low and ("_hc_" in eid_low or "_hp_" in eid_low):
            off_peak_confirmed = True

        # ═══ 5. Attributes with ranges in the sensor metadata ═══
        if any(kw in eid_low for kw in ["current_rate", "rate_period", "tariff", "teleinfo"]):
            for k, v in attrs.items():
                v_str = str(v)
                if ":" in v_str and "-" in v_str and any(h in v_str for h in ["22:", "23:", "06:", "07:"]):
                    ranges = [p.strip() for p in v_str.split(",") if ":" in p and "-" in p]
                    if ranges:
                        return ranges

    if off_peak_confirmed:
        # Save information for learning
        mem_set("rate_off_peak_confirmed", "yes")
        if ptec_entity:
            mem_set("rate_ptec_entity", ptec_entity)
            # Start learning off-peak ranges.
            mem_set("rate_learn_off_peak", "yes")
            log.info(f"🔍 Off-peak confirmed via {ptec_entity} — range learning activated")
        else:
            log.info("🔍 Off-peak confirmed by cumulative indexes, but no real-time period sensor was found")

    return None


def _rate_learn_off_peak_ranges(states):
    """Learn off-peak ranges by observing period transitions.
    Called every 5 minutes by monitoring. Deduces exact ranges within 24h."""
    if mem_get("rate_learn_off_peak") != "yes":
        return

    ptec_eid = mem_get("rate_ptec_entity")
    if not ptec_eid:
        return

    index = {e["entity_id"]: e for e in states}
    e = index.get(ptec_eid)
    if not e:
        return

    state = e["state"].lower().strip()
    now = datetime.now()
    hour_min = f"{now.hour}:{now.minute:02d}"

    # Store each peak/off-peak observation by hour
    data, nb = skill_get("learning_hc")
    if not data:
        data = {"observations": {}, "deduced_ranges": []}

    rounded_minute = 0 if now.minute < 30 else 30
    key_name = f"{now.hour:02d}:{rounded_minute:02d}"

    is_off_peak = "hc" in state
    if key_name not in data["observations"]:
        data["observations"][key_name] = {"hc": 0, "hp": 0}
    data["observations"][key_name]["hc" if is_off_peak else "hp"] += 1

    nb_obs = sum(v["hc"] + v["hp"] for v in data["observations"].values())
    if nb_obs >= 48:
        # Build the off-peak ranges.
        off_peak_hours = []
        for h_str in sorted(data["observations"].keys()):
            obs = data["observations"][h_str]
            if obs["hc"] > obs["hp"]:
                off_peak_hours.append(h_str)

        if off_peak_hours:
            # Convert to continuous ranges.
            ranges = _build_ranges(off_peak_hours)
            data["deduced_ranges"] = ranges

            # Apply to the active rate.
            rate = rate_get()
            if rate and "type" in rate:
                rate["off_peak_hours"] = ranges
                rate["hc_source"] = "auto_learned"
                rate["configured_at"] = now.isoformat()
                skill_set("pricing", rate)
                mem_set("rate_learn_off_peak", "done")
                telegram_send(
                    f"🧠 Off-peak hours auto-applied!\n"
                    f"Detected ranges: {', '.join(ranges)}\n"
                    f"Source: 24h period observation"
                )
                log.info(f"🧠 Off-peak ranges auto-applied: {ranges}")

    skill_set("learning_hc", data, nb + 1)


def _build_ranges(hours_hc):
    """Convert an off-peak hour list like ['22:00', '22:30'] into ranges like ['22:00-06:00']."""
    if not hours_hc:
        return []

    # Convert to minutes
    minutes = []
    for h in hours_hc:
        parts = h.split(":")
        minutes.append(int(parts[0]) * 60 + int(parts[1]))
    minutes.sort()

    # Find the ranges continues (with gestion of the passage minuit)
    ranges = []
    started_at = minutes[0]
    prev = minutes[0]
    for m in minutes[1:]:
        if m - prev > 30:  # Gap > 30 min = new range
            ranges.append((started_at, prev + 30))
            started_at = m
        prev = m
    ranges.append((started_at, prev + 30))

    # Convert to format HH:MM-HH:MM
    result = []
    for d, f in ranges:
        dh, dm = d // 60, d % 60
        fh, fm = f // 60, f % 60
        if fh >= 24:
            fh -= 24
        result.append(f"{dh:02d}:{dm:02d}-{fh:02d}:{fm:02d}")

    return result


def _rate_source_menu():
    return (
        "⚡ ELECTRICITY RATE SETUP\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "How should I get your electricity price?\n\n"
        "  1 → Read Home Assistant Energy / price sensors\n"
        "  2 → Enter my known rate manually\n"
        "  3 → Choose a preset provider or plan\n\n"
        "Reply with 1, 2, or 3. You can also reply cancel."
    )


def _rate_provider_menu():
    lines = []
    idx = 1
    for key_name, provider in PROVIDERS.items():
        if key_name != "other":
            lines.append(f"  {idx} → {provider['name']}")
            idx += 1
    lines.append(f"  {idx} → Other provider")
    return (
        "⚡ PRESET ELECTRICITY PROVIDERS\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "These presets are optional. If your provider is not listed, reply Other or use manual entry.\n\n"
        + "\n".join(lines)
    )


def _parse_rate_price(text):
    cleaned = (
        str(text).lower()
        .replace("$", "")
        .replace("usd", "")
        .replace("eur", "")
        .replace("€", "")
        .replace("/kwh", "")
        .replace(",", ".")
        .strip()
    )
    try:
        value = float(cleaned)
        if 0 < value < 10:
            return value
    except ValueError:
        pass
    return None


def _currency_from_unit(unit):
    unit = str(unit or "").strip()
    if "/" in unit:
        return unit.split("/", 1)[0].strip() or "currency"
    if "$" in unit:
        return "$"
    if "€" in unit or "eur" in unit.lower():
        return "€"
    return "currency"


def _rate_price_candidates_from_ha(states, energy_ids):
    candidates = []
    for entity in states or []:
        eid = entity.get("entity_id", "")
        if not eid.startswith("sensor."):
            continue
        price = _parse_rate_price(entity.get("state"))
        if price is None:
            continue
        attrs = entity.get("attributes", {})
        fname = attrs.get("friendly_name", eid)
        unit = attrs.get("unit_of_measurement", "")
        combined = f"{eid} {fname} {unit}".lower()
        if "kwh" not in combined:
            continue
        if not any(word in combined for word in ("price", "rate", "tariff", "cost", "electricity", "energy")):
            continue
        priority = 0 if eid in energy_ids else 5
        if "current" in combined or "now" in combined:
            priority -= 1
        if "total" in combined or "month" in combined or "daily" in combined:
            priority += 3
        candidates.append({
            "entity_id": eid,
            "name": fname,
            "price": price,
            "currency": _currency_from_unit(unit),
            "text": combined,
            "priority": priority,
        })
    return sorted(candidates, key=lambda c: (c["priority"], c["entity_id"]))


def _rate_configure_from_ha_energy():
    states = ha_get("states") or []
    energy_ids = _ha_energy_entity_ids()
    candidates = _rate_price_candidates_from_ha(states, energy_ids)
    if not candidates:
        telegram_send(
            "⚠️ I could not find an electricity price sensor in Home Assistant Energy.\n\n"
            "You can still reply 2 to enter your known rate manually, or 3 to choose a preset."
        )
        return True

    peak = next((c for c in candidates if "peak" in c["text"] and "off" not in c["text"]), None)
    off_peak = next((c for c in candidates if "off peak" in c["text"] or "off_peak" in c["text"] or "offpeak" in c["text"]), None)
    if peak and off_peak:
        ranges = _rate_detect_off_peak_hours() or []
        data = {
            "type": "hphc",
            "provider": "Home Assistant Energy",
            "name": "Detected peak/off-peak price sensors",
            "price_hp": peak["price"],
            "price_hc": off_peak["price"],
            "price_hp_entity_id": peak["entity_id"],
            "price_hc_entity_id": off_peak["entity_id"],
            "currency": peak["currency"],
            "source": "ha_energy",
            "configured_at": datetime.now().isoformat(),
        }
        if ranges:
            data["off_peak_hours"] = ranges
        skill_set("pricing", data)
        mem_set("pending_rate_step", "")
        msg = (
            "✅ Rate configured from Home Assistant Energy\n"
            f"Peak: {peak['price']} {peak['currency']}/kWh ({peak['name']})\n"
            f"Off-peak: {off_peak['price']} {off_peak['currency']}/kWh ({off_peak['name']})"
        )
        if ranges:
            msg += f"\nOff-peak hours: {', '.join(ranges)}"
        telegram_send(msg)
        return True

    best = candidates[0]
    data = {
        "type": "base",
        "provider": "Home Assistant Energy",
        "name": best["name"],
        "price_kwh": best["price"],
        "price_entity_id": best["entity_id"],
        "currency": best["currency"],
        "source": "ha_energy",
        "configured_at": datetime.now().isoformat(),
    }
    skill_set("pricing", data)
    mem_set("pending_rate_step", "")
    telegram_send(
        "✅ Rate configured from Home Assistant Energy\n"
        f"{best['name']}: {best['price']} {best['currency']}/kWh"
    )
    return True


def _start_rate_button_flow():
    """Legacy electricity rate button flow."""
    if not str(CFG.get("telegram_chat_id", "")).strip():
        log.info("Rate button flow deferred until Telegram chat_id is known.")
        return
    mem_set("pending_rate_step", "source")
    telegram_send(_rate_source_menu())


def _rate_button_flow_handle_response(text):
    """Handle legacy rate button-flow responses. Returns True if consumed."""
    step = mem_get("pending_rate_step")
    if not step:
        return False

    t = text.strip().lower()

    if t in ("cancel", "stop", "no"):
        mem_set("pending_rate_step", "")
        telegram_send("❌ Rate configuration cancelled.")
        return True

    if step == "source":
        if t in ("1", "ha", "home assistant", "home assistant energy", "energy", "auto"):
            return _rate_configure_from_ha_energy()
        if t in ("2", "manual", "known", "known rate"):
            mem_set("rate_temp_provider", "manual")
            mem_set("rate_temp_provider_name", "Manual")
            mem_set("pending_rate_step", "custom_type")
            telegram_send(
                "⚡ Manual electricity rate\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "What type of rate do you have?\n"
                "  1 → Single price per kWh\n"
                "  2 → Peak/off-peak prices"
            )
            return True
        if t in ("3", "preset", "provider", "plan"):
            mem_set("pending_rate_step", "provider")
            telegram_send(_rate_provider_menu())
            return True
        telegram_send("Please reply with 1, 2, or 3.\n\n" + _rate_source_menu())
        return True

    if step == "provider":
        key_names = [c for c in PROVIDERS.keys() if c != "other"]
        idx = None
        try:
            num = int(t)
            if 1 <= num <= len(key_names):
                idx = key_names[num - 1]
            elif num == len(key_names) + 1:
                idx = "other"
        except ValueError:
            # By name
            for c, f in PROVIDERS.items():
                if t in c or t in f["name"].lower():
                    idx = c
                    break

        if idx == "other":
            mem_set("rate_temp_provider", "other")
            mem_set("pending_rate_step", "custom_provider")
            telegram_send("🏢 What is the name of your electricity provider?")
            return True

        if idx is None:
            telegram_send("Provider not recognized. Reply with the number or name.")
            return True

        provider = PROVIDERS[idx]
        mem_set("rate_temp_provider", idx)

        # List the offers
        offers = provider["offers"]
        if len(offers) == 1:
            offer_key_name = list(offers.keys())[0]
            return _rate_apply_offer(idx, offer_key_name)

        msg = f"⚡ {provider['name']} — Which plan?\n\n"
        for i, (key_name, offer) in enumerate(offers.items(), 1):
            if offer["type"] == "base":
                msg += f"  {i} → {offer['name']} ({offer.get('price_kwh', '?')}/kWh)\n"
            elif offer["type"] == "hphc":
                msg += f"  {i} → {offer['name']} (peak: {offer.get('price_hp')} / off-peak: {offer.get('price_hc')})\n"
            elif offer["type"] == "tempo":
                msg += f"  {i} → {offer['name']} (blue off-peak: {offer.get('price_blue_hc')})\n"
            else:
                msg += f"  {i} → {offer['name']}\n"
        mem_set("pending_rate_step", "offer")
        telegram_send(msg)
        return True

    if step == "custom_provider":
        mem_set("rate_temp_provider_name", text.strip() or "Manual")
        mem_set("pending_rate_step", "custom_type")
        telegram_send("Rate type?\n  1 → Single price per kWh\n  2 → Peak/off-peak prices")
        return True

    if step == "custom_type":
        if t in ("1", "base"):
            mem_set("pending_rate_step", "custom_price")
            mem_set("rate_temp_type", "base")
            telegram_send("💰 What is your price per kWh?\nExample: 0.162")
        else:
            mem_set("pending_rate_step", "custom_hp")
            mem_set("rate_temp_type", "hphc")
            telegram_send("💰 Peak price per kWh?\nExample: 0.241")
        return True

    if step == "custom_price":
        try:
            price = _parse_rate_price(t)
            if price is None:
                raise ValueError("invalid price")
            data = {
                "type": "base",
                "provider": mem_get("rate_temp_provider_name") or "Manual",
                "price_kwh": price,
                "configured_at": datetime.now().isoformat()
            }
            skill_set("pricing", data)
            mem_set("pending_rate_step", "")
            telegram_send(f"✅ Rate configured\n{data['provider']} single price: {price}/kWh")
            return True
        except Exception:
            telegram_send("Invalid format. Example: 0.162")
            return True

    if step == "custom_hp":
        try:
            price = _parse_rate_price(t)
            if price is None:
                raise ValueError("invalid price")
            mem_set("rate_temp_hp", str(price))
            mem_set("pending_rate_step", "custom_hc")
            telegram_send(f"✅ Peak price: {price}/kWh\n💰 Off-peak price per kWh?")
            return True
        except Exception:
            telegram_send("Invalid format.")
            return True

    if step == "custom_hc":
        try:
            price_hc = _parse_rate_price(t)
            if price_hc is None:
                raise ValueError("invalid price")
            mem_set("rate_temp_hc", str(price_hc))
            mem_set("pending_rate_step", "custom_ranges")
            telegram_send("🕐 Off-peak hour ranges?\nExample: 22:00-06:00")
            return True
        except Exception:
            telegram_send("Invalid format.")
            return True

    if step == "custom_ranges":
        ranges = [p.strip() for p in t.replace(" ", "").split(",") if "-" in p]
        if not ranges:
            telegram_send("Invalid format. Example: 22:00-06:00")
            return True
        data = {
            "type": "hphc",
            "provider": mem_get("rate_temp_provider_name") or "Manual",
            "price_hp": float(mem_get("rate_temp_hp") or "0.27"),
            "price_hc": float(mem_get("rate_temp_hc") or "0.2068"),
            "off_peak_hours": ranges,
            "configured_at": datetime.now().isoformat()
        }
        skill_set("pricing", data)
        mem_set("pending_rate_step", "")
        telegram_send(
            f"✅ Peak/off-peak rate configured\n"
            f"{data['provider']}\n"
            f"Peak: {data['price_hp']}/kWh | Off-peak: {data['price_hc']}/kWh\n"
            f"Off-peak hours: {', '.join(ranges)}"
        )
        return True

    if step == "offer":
        provider_key_name = mem_get("rate_temp_provider")
        if provider_key_name not in PROVIDERS:
            mem_set("pending_rate_step", "")
            return False

        offers = PROVIDERS[provider_key_name]["offers"]
        offer_key_names = list(offers.keys())

        idx = None
        try:
            num = int(t)
            if 1 <= num <= len(offer_key_names):
                idx = offer_key_names[num - 1]
        except ValueError:
            for c, o in offers.items():
                if t in c or t in o["name"].lower():
                    idx = c
                    break

        if idx is None:
            telegram_send("Plan not recognized. Reply with the number.")
            return True

        return _rate_apply_offer(provider_key_name, idx)

    if step == "chosen_day":
        days_map = {"1": 0, "monday": 0, "2": 2, "wednesday": 2, "3": 4, "friday": 4}
        day = days_map.get(t)
        if day is None:
            telegram_send("Reply 1 (Monday), 2 (Wednesday) or 3 (Friday)")
            return True

        rate_in_progress = json.loads(mem_get("rate_temp_data") or "{}")
        rate_in_progress["chosen_day"] = day
        days_names = {0: "Monday", 2: "Wednesday", 4: "Friday"}

        # If peak/off-peak pricing applies, ask for the off-peak hours.
        if "hphc" in rate_in_progress.get("type", ""):
            mem_set("rate_temp_data", json.dumps(rate_in_progress))
            mem_set("pending_rate_step", "off_peak_hours")
            telegram_send(
                f"✅ Selected day: {days_names[day]}\n\n"
                f"🕐 Your off-peak hour ranges?\nExample: 22:00-06:00"
            )
            return True

        # Otherwise this setup is complete.
        rate_in_progress["configured_at"] = datetime.now().isoformat()
        skill_set("pricing", rate_in_progress)
        mem_set("pending_rate_step", "")
        telegram_send(
            f"✅ Rate configured\n"
            f"{rate_in_progress.get('provider', '')} — {rate_in_progress.get('name', '')}\n"
            f"Selected day: {days_names[day]}\n"
            f"Weekday: {rate_in_progress.get('price_weekday', '?')}/kWh\n"
            f"Weekend plus selected day: {rate_in_progress.get('price_weekend_day', '?')}/kWh"
        )
        return True

    if step == "off_peak_hours":
        ranges = [p.strip() for p in t.replace(" ", "").split(",") if "-" in p]
        if not ranges:
            telegram_send("Invalid format. Example: 22:00-06:00")
            return True

        rate_in_progress = json.loads(mem_get("rate_temp_data") or "{}")
        rate_in_progress["off_peak_hours"] = ranges
        rate_in_progress["configured_at"] = datetime.now().isoformat()
        skill_set("pricing", rate_in_progress)
        mem_set("pending_rate_step", "")

        telegram_send(
            f"✅ Rate configured\n"
            f"{rate_in_progress.get('provider', '')} — {rate_in_progress.get('name', '')}\n"
            f"Peak: {rate_in_progress.get('price_hp')} | Off-peak: {rate_in_progress.get('price_hc')}\n"
            f"Off-peak hours: {', '.join(ranges)}"
        )
        return True

    mem_set("pending_rate_step", "")
    return False


def _rate_apply_offer(provider_key_name, offer_key_name):
    """Apply a preset provider offer with prefilled rates."""
    provider = PROVIDERS[provider_key_name]
    offer = provider["offers"][offer_key_name]
    name_f = provider["name"]

    if offer["type"] == "base":
        data = {
            "type": "base",
            "provider": name_f,
            "name": offer["name"],
            "price_kwh": offer["price_kwh"],
            "subscription_month": offer.get("subscription_month", 0),
            "configured_at": datetime.now().isoformat()
        }
        skill_set("pricing", data)
        mem_set("pending_rate_step", "")
        telegram_send(
            f"✅ Rate configured automatically\n"
            f"{name_f} — {offer['name']}\n"
            f"Price: {offer['price_kwh']}/kWh"
        )
        return True

    elif offer["type"] == "hphc":
        data = {
            "type": "hphc",
            "provider": name_f,
            "name": offer["name"],
            "price_hp": offer["price_hp"],
            "price_hc": offer["price_hc"],
            "subscription_month": offer.get("subscription_month", 0),
        }
        # Search the off-peak hours automatically in HA
        hc_auto = _rate_detect_off_peak_hours()
        if hc_auto:
            data["off_peak_hours"] = hc_auto
            data["configured_at"] = datetime.now().isoformat()
            skill_set("pricing", data)
            mem_set("pending_rate_step", "")
            telegram_send(
                f"✅ {name_f} — {offer['name']}\n"
                f"Peak: {offer['price_hp']} | Off-peak: {offer['price_hc']}\n"
                f"🕐 Auto-detected off-peak hours: {', '.join(hc_auto)}"
            )
        else:
            mem_set("rate_temp_data", json.dumps(data))
            mem_set("pending_rate_step", "off_peak_hours")
            telegram_send(
                f"✅ {name_f} — {offer['name']}\n"
                f"Peak: {offer['price_hp']} | Off-peak: {offer['price_hc']}\n\n"
                f"🕐 Off-peak hours were not found in Home Assistant.\n"
                f"Check your utility meter or bill.\n"
                f"Example: 22:00-06:00"
            )
        return True

    elif offer["type"] == "tempo":
        data = {
            "type": "tempo",
            "provider": name_f,
            "name": offer["name"],
            "price_blue_hp": offer["price_blue_hp"],
            "price_blue_hc": offer["price_blue_hc"],
            "price_white_hp": offer["price_white_hp"],
            "price_white_hc": offer["price_white_hc"],
            "price_red_hp": offer["price_red_hp"],
            "price_red_hc": offer["price_red_hc"],
            "subscription_month": offer.get("subscription_month", 0),
        }
        mem_set("rate_temp_data", json.dumps(data))
        mem_set("pending_rate_step", "off_peak_hours")
        telegram_send(
            f"✅ {name_f} — {offer['name']}\n"
            f"Blue: peak {offer['price_blue_hp']} / off-peak {offer['price_blue_hc']}\n"
            f"White: peak {offer['price_white_hp']} / off-peak {offer['price_white_hc']}\n"
            f"Red: peak {offer['price_red_hp']} / off-peak {offer['price_red_hc']}\n\n"
            f"🕐 Your off-peak hour ranges?\nExample: 22:00-06:00"
        )
        return True

    elif offer["type"] in ("weekend", "weekend_hphc", "weekend_plus", "weekend_plus_hphc"):
        data = {
            "type": offer["type"],
            "provider": name_f,
            "name": offer["name"],
            "subscription_month": offer.get("subscription_month", 0),
        }
        for k, v in offer.items():
            if k.startswith("price_"):
                data[k] = v

        if "plus" in offer["type"]:
            mem_set("rate_temp_data", json.dumps(data))
            mem_set("pending_rate_step", "chosen_day")
            telegram_send(
                f"✅ {name_f} — {offer['name']}\n"
                f"Rates prefilled automatically.\n\n"
                f"📅 Which extra weekday should use the lower rate?\n"
                f"  1 → Monday\n  2 → Wednesday\n  3 → Friday"
            )
            return True

        if "hphc" in offer["type"]:
            mem_set("rate_temp_data", json.dumps(data))
            mem_set("pending_rate_step", "off_peak_hours")
            telegram_send(
                f"✅ {name_f} — {offer['name']}\n"
                f"Rates prefilled.\n\n"
                f"🕐 Your off-peak hour ranges?\nExample: 22:00-06:00"
            )
            return True

        data["configured_at"] = datetime.now().isoformat()
        skill_set("pricing", data)
        mem_set("pending_rate_step", "")
        telegram_send(
            f"✅ Rate configured\n{name_f} — {offer['name']}\n"
            f"Weekday: {data.get('price_weekday', '?')}/kWh | Weekend and holidays: {data.get('price_weekend', '?')}/kWh"
        )
        return True

    return False


def cmd_md():
    """Send the Specification by email"""
    ok = send_md_par_email()
    if ok:
        return "📧 Spec document sent by email."
    return "❌ Email send failed — check SMTP config."


def cmd_sms():
    """Resend a security code and lock the channel."""
    # global channel_locked  # via shared
    shared.channel_locked = True
    send_code_sms()
    return "📱 SMS code sent — channel locked."


def cmd_rate():
    """Show or configure the electricity rate"""
    rate = rate_get()
    current_price = rate_current_kwh_price()
    is_off_peak = rate_is_off_peak_hour()

    report = "⚡ ELECTRICITY RATE\n━━━━━━━━━━━━━━━━━━\n"
    type_label = {
        "base": "single price",
        "hphc": "peak/off-peak",
        "tempo": "variable color-day",
        "weekend": "weekend",
        "weekend_hphc": "weekend peak/off-peak",
        "weekend_plus": "weekend plus",
        "weekend_plus_hphc": "weekend plus peak/off-peak",
    }.get(rate.get("type", "base"), rate.get("type", "base"))
    currency = rate.get("currency", "")
    unit = f" {currency}/kWh" if currency else "/kWh"
    report += f"Provider: {rate.get('provider', 'Manual')}\n"
    report += f"Type: {type_label}\n"

    if rate.get("type") == "hphc":
        report += f"Peak price: {rate.get('price_hp')}{unit}\n"
        report += f"Off-peak price: {rate.get('price_hc')}{unit}\n"
        report += f"Off-peak ranges: {', '.join(rate.get('off_peak_hours', []))}\n"
        report += f"\nCurrent period: {'🔵 off-peak' if is_off_peak else '🔴 peak'} — {current_price}{unit}"
    else:
        report += f"Price: {rate.get('price_kwh', current_price)}{unit}\n"

    if rate.get("configured_at"):
        report += f"\nConfigured on: {rate['configured_at'][:10]}"

    report += "\n\nTo modify, say your new electricity rate in chat."
    return report


def skill_dynamic_collect(states):
    """Run all registered dynamic skills."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT name, data FROM skills WHERE name LIKE 'dyn_%'"
    ).fetchall()
    conn.close()

    index = {e["entity_id"]: e for e in states}
    now = datetime.now()

    for name, data_json in rows:
        try:
            definition = json.loads(data_json)
            entities = definition.get("entities", [])
            threshold = definition.get("threshold", None)
            action = definition.get("action", "collect")  # collect | alert | compare
            description = definition.get("description", name)

            value_texts = {}
            for eid in entities:
                e = index.get(eid)
                if e and e["state"] not in ("unavailable", "unknown"):
                    try:
                        value_texts[eid] = float(e["state"])
                    except Exception:
                        value_texts[eid] = e["state"]

            if not value_texts:
                continue

            if action == "collect":
                # Store values in the history of the skill
                history = definition.get("history", [])
                history.append({
                    "timestamp": now.isoformat(),
                    "value_texts": value_texts
                })
                definition["history"] = history[-200:]
                skill_set(name, definition)

            elif action == "alert" and threshold is not None:
                for eid, val in value_texts.items():
                    if isinstance(val, (int, float)) and val > float(threshold):
                        fname = index[eid].get("attributes", {}).get("friendly_name", eid)
                        _alert_if_new(
                            f"dyn_{name}_{eid}",
                            f"🧠 SKILL {description}\n{fname} = {val} (threshold: {threshold})",
                            delay_h=6
                        )

            elif action == "compare":
                if len(entities) >= 2:
                    v1 = value_texts.get(entities[0])
                    v2 = value_texts.get(entities[1])
                    if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
                        ratio = v1 / v2 if v2 > 0 else 0
                        history = definition.get("history", [])
                        history.append({
                            "timestamp": now.isoformat(),
                            "ratio": round(ratio, 3),
                            "v1": v1, "v2": v2
                        })
                        definition["history"] = history[-200:]
                        skill_set(name, definition)

        except Exception as ex:
            log.error(f"❌ Dynamic skill {name}: {ex}")


def skill_create_auto(question, states):
    """lightweight model decides whether a new skill is necessary and creates it."""
    index = {e["entity_id"]: e for e in states}

    conn = sqlite3.connect(DB_PATH)
    nb_dyn = conn.execute("SELECT COUNT(*) FROM skills WHERE name LIKE 'dyn_%'").fetchone()[0]
    conn.close()
    if nb_dyn >= 10:
        return None  # Max 10 dynamic skills.

    prompt = (
        "You are the user's home automation assistant.\n"
        "The user asked this question: \"" + question + "\"\n\n"
        "Should you create a new monitoring skill for future requests?\n"
        "A skill monitors Home Assistant entities and learns a pattern.\n\n"
        "Reply ONLY in JSON:\n"
        "If NO: {\"create\": false}\n"
        "If YES: {\"create\": true, \"name\": \"dyn_name_short\", \"description\": \"what it does\", "
        "\"entities\": [\"sensor.xxx\", \"sensor.yyy\"], \"action\": \"collect\", \"threshold\": null}\n\n"
        "Possible actions: collect (history), alert (threshold exceeded), compare (ratio between 2 entities).\n"
        "IMPORTANT: the entities must exist in Home Assistant.\n"
        "Reply with JUST the JSON, nothing else."
    )

    try:
        blocks, t_in, t_out = llm_provider.llm_completion(
            CFG, [{"role": "user", "content": prompt}],
            max_tokens=500
        )
        log_token_usage(t_in, t_out)
        text = llm_provider.stream_text(blocks).strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(text)

        if not result.get("create"):
            return None

        name = result.get("name", "")
        if not name.startswith("dyn_"):
            name = f"dyn_{name}"

        entities = result.get("entities", [])
        valid_entities = [eid for eid in entities if eid in index]
        if not valid_entities:
            return None

        definition = {
            "description": result.get("description", name),
            "entities": valid_entities,
            "action": result.get("action", "collect"),
            "threshold": result.get("threshold"),
            "cree_le": datetime.now().isoformat(),
            "created_by": "auto",
            "history": []
        }

        skill_set(name, definition, 0)
        log.info(f"🧠 New skill created: {name} — {definition['description']}")
        telegram_send(
            f"🧠 NEW SKILL CREATED\n"
            f"Name : {name}\n"
            f"Role: {definition['description']}\n"
            f"Entities: {', '.join(valid_entities)}\n"
            f"Action : {definition['action']}"
        )
        return name

    except Exception as ex:
        log.error(f"❌ skill_create_auto: {ex}")
        return None


def cmd_audit():
    telegram_send("🔍 Audit running...")
    states = ha_get("states")
    if not states:
        return "❌ AUDIT — HA unreachable"

    total = len(states)
    offline = [e for e in states if e["state"] in ["unavailable", "unknown"]]
    domains_ko = {}
    for e in offline:
        d = e["entity_id"].split(".")[0]
        domains_ko.setdefault(d, []).append(e["entity_id"])

    report  = f"📊 AUDIT HOME ASSISTANT\n━━━━━━━━━━━━━━━━━━━━\n"
    report += f"Total : {total} | ✅ {total - len(offline)} | ❌ {len(offline)}\n"

    if offline:
        for domain, ids in sorted(domains_ko.items()):
            report += f"\n[{domain}]\n"
            for eid in ids[:5]:
                report += f"  • {eid}\n"
            if len(ids) > 5:
                report += f"  ... and {len(ids)-5} others\n"

    context = ha_get_context_intelligent("audit general state home", states)
    prompt = (
        "OFFLINE ENTITIES :\n"
        + "\n".join(f"  • {e['entity_id']}" for e in offline[:20])
        + "\n\nTo chacune, a line : normal or abnormal ? Sois concis."
    )
    analysis = call_llm(prompt, context)
    report += f"\n🤖 {analysis}"
    return report


def cmd_energy(detail=False):
    states = ha_get("states")
    if not states:
        return "❌ ENERGY — HA unreachable"

    index = {e["entity_id"]: e for e in states}
    now_str = datetime.now().strftime("%H:%M — %A %d/%m/%Y")

    def _val(eid, default="?"):
        e = index.get(eid)
        if e and e["state"] not in ("unavailable", "unknown"):
            return e["state"]
        return default

    def _val_unit(eid):
        e = index.get(eid)
        if e and e["state"] not in ("unavailable", "unknown"):
            unit = e.get("attributes", {}).get("unit_of_measurement", "")
            return f"{e['state']} {unit}".strip()
        return "—"

    report = f"⚡ SUMMARY ENERGY\n{now_str}\n━━━━━━━━━━━━━━━━━━\n"

    _has_solar = role_get("solar_production_w")
    _has_battery = role_get("battery_soc") or role_get("battery_soc_anker")

    if _has_solar:
        report += "\n☀️ SOLAR\n"
        ecu_w = role_value("solar_production_w", index, "0")
        ecu_kwh = role_value("solar_production_kwh", index, "0")
        ecu_inv = role_value("inverters_total", index, None)
        ecu_inv_on = role_value("inverters_online", index, None)
        report += f"  Production : {ecu_w} W | Today : {ecu_kwh} kWh\n"
        if ecu_inv and ecu_inv_on:
            if str(ecu_inv) != str(ecu_inv_on):
                is_day = ha_is_day(states)
                if not is_day or (ecu_w in ("0", "?") and str(ecu_inv_on) in ("0", "1")):
                    report += f"  🌙 Inverters: {ecu_inv_on}/{ecu_inv} (night standby)\n"
                else:
                    report += f"  🚨 Inverters: {ecu_inv_on}/{ecu_inv} online\n"
            else:
                report += f"  ✅ Inverters: {ecu_inv_on}/{ecu_inv} online\n"

    if _has_battery:
        report += "\n🔋 BATTERY\n"
        anker_soc = role_value("battery_soc", index, None) or role_value("battery_soc_anker", index, None)
        if anker_soc and anker_soc != "?":
            try:
                soc_val = float(anker_soc) if anker_soc not in ("?", None) else 0
                soc_icone = "🟢" if soc_val >= 80 else ("🟡" if soc_val >= 30 else "🔴")
                report += f"  {soc_icone} SOC : {anker_soc}%\n"
            except (ValueError, TypeError):
                report += f"  🔋 SOC: {anker_soc}% (no-numeric value)\n"
        anker_prod = role_value("battery_prod_solar", index, None)
        if anker_prod and anker_prod != "?":
            report += f"  ☀️ Charge solar : {anker_prod} W\n"
        anker_output = role_value("battery_output", index, None)
        if anker_output and anker_output != "?":
            report += f"  🏠 Injection home : {anker_output} W\n"
        anker_mode = role_value("battery_mode", index, None)
        if anker_mode and anker_mode != "?":
            report += f"  Mode : {anker_mode}\n"
        anker_plug_eid = role_get("battery_power")
        if anker_plug_eid:
            e_plug = index.get(anker_plug_eid)
            if e_plug and e_plug["state"] not in ("unavailable", "unknown"):
                try:
                    w_val = float(e_plug["state"])
                    if w_val < 0:
                        report += f"  ⚡ Discharge: {abs(int(w_val))} W (battery priority)\n"
                    else:
                        report += f"  🔌 Plug : {int(w_val)} W\n"
                except (ValueError, TypeError):
                    report += f"  🔌 Plug : {e_plug['state']} W\n"

    if _has_solar:
        try:
            production_w = ha_get_current_solar_production(states)
            consumption_rt = role_value("realtime_consumption", index, None)
            if consumption_rt and consumption_rt not in ("?", None) and production_w > 0:
                grid_w = float(consumption_rt)
                total_consumption = grid_w + production_w
                if total_consumption > 0:
                    cov = min(100, int(production_w / total_consumption * 100))
                    report += f"\n  ☀️ Solar coverage: {cov}%\n"
        except Exception:
            pass

    report += "\n🔌 CONSUMPTION\n"
    eco_rt = role_value("realtime_consumption", index)
    eco_day = role_value("consumption_day_cost", index)
    consumption_kwh = role_value("consumption_day_kwh", index)
    if eco_rt != "?":
        report += f"  Real-time (grid): {eco_rt} W\n"
    if eco_day != "?":
        report += f"  Daily cost: {eco_day} €\n"
    if consumption_kwh != "?":
        report += f"  Total consumption : {consumption_kwh} kWh\n"

    _has_heat_pump = role_get("heat_pump_climate")
    if _has_heat_pump:
        report += "\n🌡️ HEATING\n"
        for e in states:
            if e["entity_id"].startswith("climate."):
                carto = entity_map_get(e["entity_id"])
                if carto and "heating" in carto[0]:
                    state = e["state"]
                    attrs = e.get("attributes", {})
                    water_temp = attrs.get("current_temperature", "?")
                    temp_consigne = attrs.get("temperature", "?")
                    if state in ["auto", "heat", "cool", "fan_only", "heat_cool"]:
                        report += f"  ✅ heat pump : RUNNING (mode {state})\n"
                    else:
                        report += f"  ⚫ heat pump : {state}\n"
                    report += f"  Water: {water_temp}°C | Setpoint: {temp_consigne}°C\n"
                    break
        heat_pump_energy = role_value("heat_pump_consumption", index)
        if heat_pump_energy != "?":
            report += f"  Consumption heat pump : {heat_pump_energy} W\n"

    temp_int = role_value("indoor_temperature", index)
    temp_ext = role_value("outdoor_temperature", index)
    if temp_int != "?" or temp_ext != "?":
        if not _has_heat_pump:
            report += "\n🌡️ TEMPERATURES\n"
        if temp_int != "?":
            report += f"  Indoor temp: {temp_int}°C\n"
        if temp_ext != "?":
            report += f"  Outdoor temp: {temp_ext}°C\n"

    active_cycles = []
    for eid, state_p in _state_plugs.items():
        if state_p == "active":
            e = index.get(eid)
            fname = e.get("attributes", {}).get("friendly_name", eid) if e else eid
            in_progress = cycle_in_progress(eid)
            duration = ""
            if in_progress:
                started_at_dt = datetime.fromisoformat(in_progress[0])
                mins = int((datetime.now() - started_at_dt).total_seconds() / 60)
                duration = f" ({mins} min)"
            active_cycles.append(f"{fname}{duration}")
    if active_cycles:
        report += "\n🔄 ACTIVE CYCLES\n"
        for c in active_cycles:
            report += f"  ▶️ {c}\n"

    for e in states:
        if e["entity_id"].startswith("weather."):
            attrs = e.get("attributes", {})
            temp = attrs.get("temperature", "?")
            hum = attrs.get("humidity", "?")
            report += f"\n🌤️ WEATHER: {temp}°C | Humidity {hum}%\n"
            break

    try:
        graph_bytes = generate_energy_graph(states, index)
        if graph_bytes:
            telegram_send_photo(graph_bytes, "⚡ Energy of the day")
    except Exception:
        pass

    if not detail:
        report += "\n💡 /energy detail → report complete"
        return report

    report += "\n━━━━━━━━━━━━━━━━━━\n📋 DETAIL COMPLET\n"

    # Every the plugs with power
    report += "\n🔌 CONNECTED PLUGS\n"
    plugs = entity_map_get_by_category("connected_plug")
    for eid, sc, pc in plugs:
        if not eid.startswith("sensor."):
            continue
        e = index.get(eid)
        if not e:
            continue
        unit = e.get("attributes", {}).get("unit_of_measurement", "")
        if unit not in ("W", "w", "Watt"):
            continue
        fname = e.get("attributes", {}).get("friendly_name", eid)
        for suffix in [" Power", " Power", " Consumption"]:
            if fname.endswith(suffix):
                fname = fname[:-len(suffix)].strip()
                break
        try:
            val = float(e["state"])
            cycle_active = _state_plugs.get(eid) == "active"
            app = appliance_get(eid)
            app_name = app["name"] if app and app.get("name") else fname
            app_type = app["type"] if app else ""

            if cycle_active:
                icon = "🔵"
                status = f" [cycle running]"
            elif val > 5:
                icon = "🟢"
                status = ""
            else:
                icon = "⚫"
                status = ""

            # Exclude monitoring plugs from the active display"
            if app_type == "energy_monitor" and not cycle_active:
                icon = "📊"
                status = " [monitoring]"
            elif app_type == "ignore":
                continue

            report += f"  {icon} {app_name} : {int(val)} W{status}\n"
        except Exception:
            report += f"  ❓ {fname} : {e['state']}\n"

    report += "\n☀️ SOLAR ENTITIES SOLAR\n"
    cats_detail = ["energy_solar", "energy_battery", "energy_production"]
    for cat in cats_detail:
        entities_cat = entity_map_get_by_category(cat)
        if entities_cat:
            report += f"  [{cat}]\n"
            for eid, sc, pc in entities_cat:
                e = index.get(eid)
                if e:
                    unit = e.get("attributes", {}).get("unit_of_measurement", "")
                    state = e["state"]
                    icon = "❌" if state in ("unavailable", "unknown") else "✅"
                    report += f"    {icon} {eid} = {state} {unit}\n"

    # Heating complete
    report += "\n🌡️ FULL HEATING\n"
    entities_chauff = entity_map_get_by_category("energy_heating")
    for eid, sc, pc in entities_chauff:
        e = index.get(eid)
        if e:
            unit = e.get("attributes", {}).get("unit_of_measurement", "")
            state = e["state"]
            icon = "❌" if state in ("unavailable", "unknown") else "✅"
            report += f"  {icon} {eid} = {state} {unit}\n"

    return report


def cmd_solar():
    # No failures -> clear message.
    if not role_get("solar_production_w"):
        return "☀️ No solar panels detected.\nIf your just installed panels, run /scan to detect them."

    states = ha_get("states")
    if not states:
        return "❌ SOLAR — HA unreachable"
    index = {e["entity_id"]: e for e in states}
    entities = entity_map_get_by_category("energy_solar")
    report = "☀️ PRODUCTION SOLAR\n━━━━━━━━━━━━━━━━━━\n"
    if entities:
        for eid, subcategory, room in entities:
            if eid in index:
                e = index[eid]
                unit = e.get("attributes", {}).get("unit_of_measurement", "")
                report += f"  {eid} = {e['state']} {unit}\n"
    else:
        report += "No solar sensor mapped — run `scan`\n"

    # Graphique solar
    try:
        graph_bytes = generate_energy_graph(states, index)
        if graph_bytes:
            telegram_send_photo(graph_bytes, "☀️ Solar of the day")
    except Exception:
        pass

    return report


def cmd_batteries():
    states = ha_get("states")
    if not states:
        return "❌ BATTERIES — HA unreachable"
    batteries = []
    for e in states:
        eid = e["entity_id"]
        is_battery = (
            "battery" in eid.lower() or "battery" in eid.lower() or
            e.get("attributes", {}).get("device_class") == "battery" or
            "state_of_charge" in eid.lower()
        )
        if not is_battery:
            continue
        try:
            val = float(e["state"])
            carto = entity_map_get(eid)
            room = carto[2] if carto else ""
            batteries.append((eid, room, int(val)))
        except Exception:
            continue
    batteries.sort(key=lambda x: x[2])
    report = "🔋 BATTERY STATUS\n━━━━━━━━━━━━━━━━━━\n"
    for eid, room, val in batteries:
        icon = "🚨" if val < 10 else ("⚠️" if val < 20 else ("🟡" if val < 50 else "✅"))
        room_str = f" [{room}]" if room else ""
        report += f"{icon} {eid}{room_str} : {val}%\n"
    return report if len(batteries) > 0 else "🔋 No battery detected"


def cmd_zigbee():
    states = ha_get("states")
    if not states:
        return "❌ ZIGBEE — HA unreachable"
    index = {e["entity_id"]: e for e in states}

    # Collect TOUS the devices Zigbee via linkquality
    devices = []  # (eid, fname, room, lqi, state)
    seen_devices = set()  # Avoid duplicates by physical device
    for e in states:
        lqi = e.get("attributes", {}).get("linkquality")
        if lqi is None:
            continue
        eid = e["entity_id"]
        device_key = eid.split(".", 1)[1] if "." in eid else eid
        base_key = device_key
        for suffix in ["_power", "_current", "_voltage", "_energy", "_power", "_battery"]:
            if base_key.endswith(suffix):
                base_key = base_key[:-len(suffix)]
                break
        if base_key in seen_devices:
            continue
        seen_devices.add(base_key)

        attrs = e.get("attributes", {})
        fname = attrs.get("friendly_name", eid)
        carto = entity_map_get(eid)
        room = carto[2] if carto else ""
        try:
            lqi_val = int(lqi)
        except Exception:
            lqi_val = -1
        devices.append((eid, fname, room, lqi_val, e["state"]))

    # Sort by increasing LQI (weakest first)
    devices.sort(key=lambda x: x[3])

    total = len(devices)
    ko = [d for d in devices if d[4] in ("unavailable", "unknown")]
    criticals = [d for d in devices if 0 <= d[3] <= 30 and d[4] not in ("unavailable", "unknown")]
    weak = [d for d in devices if 30 < d[3] <= 50 and d[4] not in ("unavailable", "unknown")]
    bons = [d for d in devices if 50 < d[3] <= 100 and d[4] not in ("unavailable", "unknown")]
    excellents = [d for d in devices if d[3] > 100 and d[4] not in ("unavailable", "unknown")]

    report = f"📡 RESEAU ZIGBEE — {total} devices\n━━━━━━━━━━━━━━━━━━\n"

    # Hors line
    if ko:
        report += f"\n❌ OFFLINE ({len(ko)})\n"
        for eid, fname, room, lqi, state in ko:
            room_str = f" [{room}]" if room else ""
            report += f"  {fname}{room_str}\n"
    else:
        report += "\n✅ Tors online\n"

    # LQI critical
    if criticals:
        report += f"\n🚨 LQI CRITIQUE ≤30 ({len(criticals)})\n"
        for eid, fname, room, lqi, state in criticals:
            room_str = f" [{room}]" if room else ""
            report += f"  LQI={lqi} — {fname}{room_str}\n"

    # Low LQI
    if weak:
        report += f"\n⚠️ LQI WEAK 31-50 ({len(weak)})\n"
        for eid, fname, room, lqi, state in weak:
            room_str = f" [{room}]" if room else ""
            report += f"  LQI={lqi} — {fname}{room_str}\n"

    if bons or excellents:
        report += f"\n✅ Good LQI 51-100: {len(bons)} devices"
        report += f"\n✅ LQI EXCELLENT >100 : {len(excellents)} devices\n"

    # Top 5 meiltheir and 5 pires (online)
    online_devices = [d for d in devices if d[4] not in ("unavailable", "unknown") and d[3] >= 0]
    if len(online_devices) >= 5:
        report += "\n📊 TOP 5 meiltheir :\n"
        for eid, fname, room, lqi, state in sorted(online_devices, key=lambda x: -x[3])[:5]:
            report += f"  LQI={lqi} — {fname}\n"
        report += "\n📊 TOP 5 weakest :\n"
        for eid, fname, room, lqi, state in sorted(online_devices, key=lambda x: x[3])[:5]:
            room_str = f" [{room}]" if room else ""
            report += f"  LQI={lqi} — {fname}{room_str}\n"

    return report


def cmd_nas():
    states = ha_get("states")
    if not states:
        return "❌ NAS — HA unreachable"
    index = {e["entity_id"]: e for e in states}
    entities_nas = entity_map_get_by_category("nas")
    report = "🗄️ NAS SYNOLOGY\n━━━━━━━━━━━━━━\n"
    if not entities_nas:
        return report + "No NAS mapped — run `scan`"
    for eid, subcategory, room in entities_nas:
        if eid in index:
            e = index[eid]
            unit = e.get("attributes", {}).get("unit_of_measurement", "")
            report += f"  {_ha_entity_display(e, room)} = {e['state']} {unit}\n"
    return report


def cmd_automations():
    states = ha_get("states")
    if not states:
        return "❌ AUTOMATISATIONS — HA unreachable"
    autos = [e for e in states if e["entity_id"].startswith("automation.")]
    active_items = [e for e in autos if e["state"] == "on"]
    inactive_items = [e for e in autos if e["state"] == "off"]
    report  = f"⚙️ AUTOMATISATIONS\n━━━━━━━━━━━━━━━\n"
    report += f"Total: {len(autos)} | Active: {len(active_items)} | Inactive: {len(inactive_items)}\n"
    report += "(unavailable = conditionnel, normal)\n"
    if inactive_items:
        report += "\n⚠️ Disabled:\n"
        for e in inactive_items[:10]:
            report += f"  • {_ha_entity_label(e)}\n"
    return report


def cmd_addons():
    states = ha_get("states")
    if not states:
        return "❌ ADD-ONS — HA unreachable"
    updates = [e for e in states if e["entity_id"].startswith("update.")]
    report = "🧩 APPS\n━━━━━━━━━━\n"
    for e in updates[:20]:
        name  = e.get("attributes", {}).get("friendly_name", e["entity_id"])
        state = "🔄 Update available" if e["state"] == "on" else "✅"
        report += f"  {state} {name}\n"
    return report


def cmd_budget():
    tokens_in, tokens_out = get_token_usage()
    cost = (tokens_in * 0.000001) + (tokens_out * 0.000005)
    budget = CFG.get("llm_monthly_budget_usd", 0)
    pct = (cost / budget * 100) if budget > 0 else 0
    remaining = max(0, budget - cost) if budget > 0 else None

    if budget <= 0:
        icon = "ℹ️"
        status = "NO INTERNAL CAP"
    elif pct >= 100:
        icon = "🛑"
        status = "EXCEEDED — AI commands disabled"
    elif pct >= 90:
        icon = "🚨"
        status = "CRITICAL"
    elif pct >= 80:
        icon = "⚠️"
        status = "WARNING"
    elif pct >= 50:
        icon = "📊"
        status = "HALFWAY"
    else:
        icon = "✅"
        status = "OK"

    return (
        f"💰 BUDGET API — {icon} {status}\n━━━━━━━━━━━━\n"
        f"Tokens in  : {tokens_in:,}\n"
        f"Tokens out : {tokens_out:,}\n"
        f"Cost       : ${cost:.3f}\n"
        f"Budget     : {'provider-managed' if budget <= 0 else f'${budget}'}\n"
        f"Remaining  : {'n/a' if remaining is None else f'${remaining:.3f}'}\n"
        f"Usage      : {'n/a' if budget <= 0 else f'{pct:.1f}%'}"
    )


def cmd_debug():
    """Diagnostic developpeur"""
    now = datetime.now()
    anomalies = []

    last_mon = _watchdog.get("monitoring_last_run")
    if last_mon and (now - last_mon).total_seconds() > 900:
        anomalies.append(f"⚠️ Thread monitoring silent since {int((now-last_mon).total_seconds()//60)} min")

    last_pri = _watchdog.get("plugs_last_run")
    if last_pri and (now - last_pri).total_seconds() > 600:
        anomalies.append(f"⚠️ Thread plugs silent since {int((now-last_pri).total_seconds()//60)} min")

    blocked = _watchdog.get("offset_blocked_since")
    if blocked and (now - blocked).total_seconds() > 300:
        anomalies.append(f"🚨 Offset Telegram blocked since {int((now-blocked).total_seconds()//60)} min")

    errors = _watchdog.get("errors", [])
    if len(errors) >= 3:
        anomalies.append(f"🚨 {len(errors)} exceptions — last : {errors[-1][1][:80]}")

    tokens_in, tokens_out = get_token_usage()
    budget = CFG.get("budget_monthly", 10)
    cost = (tokens_in * 0.000001) + (tokens_out * 0.000005)
    if cost >= budget * 0.8:
        anomalies.append(f"⚠️ Budget tokens : {cost:.2f}€ / {budget}€ ({int(cost/budget*100)}%)")

    if not anomalies:
        return f"🔧 DEBUG — v{VERSION}\n✅ No anomaly interne\nHour VM : {now.strftime('%d/%m/%Y %H:%M:%S')}"

    report = f"🔧 DEBUG — v{VERSION}\n━━━━━━━━━━━━━━━━━━━━\n"
    report += "\n".join(anomalies)
    report += f"\n\nHour VM : {now.strftime('%d/%m/%Y %H:%M:%S')}"
    return report


def cmd_logs():
    try:
        with open(LOG_PATH) as f:
            lines = f.readlines()
        return "📋 LOGS:\n" + "".join(lines[-20:])
    except Exception as e:
        return f"❌ Logs: {e}"


def cmd_memory_store():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        'SELECT key_name, value_text FROM memory_store ORDER BY updated_at DESC LIMIT 20'
    ).fetchall()
    conn.close()
    report = "🧠 MEMORY\n━━━━━━━━━━\n"
    for key_name, value_text in rows:
        report += f"  {key_name}: {value_text[:50]}\n"
    return report


def cmd_baselines():
    """Show learned behavior baselines."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT entity_id, COUNT(*), AVG(avg_value) FROM baselines GROUP BY entity_id ORDER BY COUNT(*) DESC LIMIT 20"
    ).fetchall()
    conn.close()
    if not rows:
        return "📊 No baselines learned yet."
    report = "📊 BEHAVIOR BASELINES\n━━━━━━━━━━━━━━━━━━\n"
    for entity_id, count, avg_value in rows:
        report += f"\n{entity_id}\n  {count} samples | average {avg_value:.1f}"
    return report


def cmd_scan():
    """Start a scan complete and send directement the result."""
    telegram_send("🔍 Home Assistant scan running...")
    try:
        ha_refresh_areas()
        nb_areas = len(shared._areas_id_to_name)

        conn = sqlite3.connect(DB_PATH)
        for sql in [
            "UPDATE entity_map SET category='ignore' WHERE entity_id LIKE 'select.plug_%'",
            "UPDATE entity_map SET category='ignore' WHERE entity_id LIKE 'number.plug_%'",
            "UPDATE entity_map SET category='ignore' WHERE entity_id LIKE 'update.plug_%'",
            "UPDATE entity_map SET category='ignore' WHERE entity_id LIKE 'button.plug_%'",
            "UPDATE entity_map SET category='ignore' WHERE entity_id LIKE 'switch.plug_%_child_lock'",
            "UPDATE entity_map SET category='ignore' WHERE entity_id LIKE 'sensor.plug_%_current'",
            "UPDATE entity_map SET category='ignore' WHERE entity_id LIKE 'sensor.plug_%_voltage'",
            "UPDATE entity_map SET category='ignore' WHERE entity_id LIKE 'sensor.plug_%_energy'",
            "UPDATE entity_map SET category='ignore' WHERE entity_id LIKE 'automation.plug_%'",
            "UPDATE entity_map SET category='connected_plug', subcategory='power' WHERE entity_id LIKE 'sensor.plug_%_power'",
            "UPDATE entity_map SET category='connected_plug', subcategory='command' WHERE entity_id LIKE 'switch.plug_%' AND entity_id NOT LIKE '%_child_lock'",
        ]:
            conn.execute(sql)
        conn.commit()
        conn.close()
        log.info("✅ Plugs recategorized in database")

        states = ha_get("states")
        if not states:
            telegram_send("❌ Scan cannot run — Home Assistant is unreachable")
            return ""
        nb_entities = len(states)
        index = {e["entity_id"]: e for e in states}

        conn = sqlite3.connect(DB_PATH)
        for e in states:
            conn.execute(
                "INSERT OR REPLACE INTO entities (entity_id, state, attributes, updated_at) VALUES (?, ?, ?, ?)",
                (e["entity_id"], e["state"], json.dumps(e.get("attributes", {})), datetime.now().isoformat())
            )
        count_before = conn.execute("SELECT COUNT(*) FROM entity_map").fetchone()[0]
        conn.commit()
        conn.close()

        mem_set("ha_scan_date", datetime.now().isoformat())
        mem_set("ha_entities_count", nb_entities)

        discover_automatically(states)

        conn = sqlite3.connect(DB_PATH)
        count_after = conn.execute("SELECT COUNT(*) FROM entity_map").fetchone()[0]
        all_plugs = conn.execute(
            "SELECT entity_id FROM entity_map WHERE category='connected_plug' AND entity_id LIKE 'sensor.%'"
        ).fetchall()
        nb_plugs = sum(
            1 for (eid,) in all_plugs
            if index.get(eid, {}).get("attributes", {}).get("unit_of_measurement", "") in ["W", "w", "Watt"]
        )
        conn.close()

        new_items = max(0, count_after - count_before)
        handle_pending_entities(index)

        telegram_send(
            f"✅ HA SCAN — {nb_entities} entities\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Categorized: {count_after}\n"
            f"Norvelles    : {new_items}\n"
            f"Plugs (power): {nb_plugs} monitorses\n"
            f"HA Rooms:    {nb_areas} areas loaded"
        )
        return ""
    except Exception as ex:
        log.error(f"❌ cmd_scan: {ex}")
        return f"❌ Error scan : {ex}"


def cmd_calendar():
    """Show Home Assistant calendar events that are useful for recommendations."""
    if not CFG.get("ha_url"):
        return "❌ HA not configured"

    states = ha_get("states")
    if not states:
        return "❌ HA unreachable"

    calendars = [e for e in states if e["entity_id"].startswith("calendar.")]
    if not calendars:
        return "📅 No calendar detected in HA.\nIntegrate Google Calendar, CalDAV or Local Calendar."

    report = "📅 CALENDARS HA\n━━━━━━━━━━━━━━━━━━\n"
    for cal in calendars:
        attrs = cal.get("attributes", {})
        fname = attrs.get("friendly_name", cal["entity_id"])
        state = cal["state"]  # on = event in progress
        message = attrs.get("message", "")
        start = attrs.get("start_time", "")
        end = attrs.get("end_time", "")

        icon = "🟢" if state == "on" else "⚪"
        report += f"\n{icon} {fname}"
        if state == "on" and message:
            report += f"\n  📌 {message}"
            if start:
                report += f"\n  ⏰ {start[:16]} → {end[:16] if end else '?'}"
        elif state == "off":
            if message:
                report += f"\n  Next: {message}"
                if start:
                    report += f" ({start[:16]})"
            else:
                report += f"\n  No upcoming events"
        report += "\n"

    report += "\n💡 The assistant uses the calendar to optimize recommendations :"
    report += "\n  • Absent → reporter the alerts no criticals"
    report += "\n  • Present → suggest appliances at the right time"
    return report


def _ha_get_calendar_events():
    """Retrieves the events of the next 24 hours since the calendars HA."""
    events = []
    try:
        states = ha_get("states")
        if not states:
            return events
        for e in states:
            if e["entity_id"].startswith("calendar.") and e["state"] == "on":
                attrs = e.get("attributes", {})
                events.append({
                    "calendar": attrs.get("friendly_name", e["entity_id"]),
                    "message": attrs.get("message", ""),
                    "start": attrs.get("start_time", ""),
                    "end": attrs.get("end_time", ""),
                })
    except Exception:
        pass
    return events


def cmd_dashboard():
    """Push AI Assistant stats to HA as sensors.
    Users can display these sensors in Lovelace."""
    if not CFG.get("ha_url") or not CFG.get("ha_token"):
        return "❌ HA not configured"

    headers = {"Authorization": f"Bearer {CFG['ha_token']}", "Content-Type": "application/json"}
    url_base = f"{CFG['ha_url']}/api/states"
    pushed = 0

    # 1. ROI
    eco = get_savings_month()
    month = datetime.now().strftime("%Y-%m")
    conn = sqlite3.connect(DB_PATH)
    tokens_row = conn.execute("SELECT tokens_in, tokens_out FROM tokens WHERE month=?", (month,)).fetchone()
    conn.close()
    total_tokens = (tokens_row[0] + tokens_row[1]) if tokens_row else 0
    cost_tokens = round(total_tokens * 0.000001, 2)
    roi = round(eco["total_eur"] / max(cost_tokens, 0.01), 1)

    sensors = {
        "sensor.assistant_savings_month": {
            "state": round(eco["total_eur"], 2),
            "attributes": {"unit_of_measurement": "€", "friendly_name": "AI Assistant Savings month", "icon": "mdi:piggy-bank",
                           "nb_actions": eco["nb_actions"], "kwh": round(eco["total_kwh"], 1)}
        },
        "sensor.assistant_cost_tokens": {
            "state": cost_tokens,
            "attributes": {"unit_of_measurement": "€", "friendly_name": "AI Assistant Cost tokens", "icon": "mdi:currency-eur",
                           "tokens": total_tokens}
        },
        "sensor.assistant_roi": {
            "state": roi,
            "attributes": {"unit_of_measurement": "x", "friendly_name": "AI Assistant ROI", "icon": "mdi:chart-line"}
        },
    }

    # 2. Lasts cycles
    conn = sqlite3.connect(DB_PATH)
    last_cycle = conn.execute(
        "SELECT friendly_name, duration_min, consumption_kwh, cost_eur FROM appliance_cycles WHERE ended_at IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if last_cycle:
        sensors["sensor.assistant_last_cycle"] = {
            "state": last_cycle[0],
            "attributes": {"friendly_name": "AI Assistant Last cycle", "icon": "mdi:washing-machine",
                           "duration_min": last_cycle[1], "consumption_kwh": last_cycle[2], "cost_eur": last_cycle[3]}
        }

    for eid, data_s in sensors.items():
        try:
            r = requests.post(f"{url_base}/{eid}", json=data_s, headers=headers, verify=False, timeout=5)
            if r.status_code in (200, 201):
                pushed += 1
        except Exception:
            pass

    return f"📊 Dashboard: {pushed} sensors pushed to HA\n💡 Add them to Lovelace for a visual dashboard."


def cmd_profile():
    """Show the household profile used by skills."""
    data, _ = skill_get("household")
    if not data:
        setup_data, _ = skill_get("conversational_setup")
        notes = setup_data.get("notes", []) if isinstance(setup_data, dict) else []
        if notes:
            report = "👥 HOME CONTEXT FROM CHAT\n━━━━━━━━━━━━━━━━━━\n"
            for note in notes[-10:]:
                if isinstance(note, dict) and note.get("text"):
                    report += f"  • {note['text']}\n"
            report += "\nNormal chat messages update this context over time."
            return report
        return (
            "👥 No home context saved yet.\n"
            "Normal chat messages about your home will be remembered here."
        )

    labels = {
        "household_people": "👥 People",
        "household_presence": "🏠 Weekday presence",
        "household_solar": "☀️ Solar panels",
        "household_solar_kwc": "☀️ Installed capacity",
        "household_heating": "🌡️ Heating",
        "household_hot_water": "🚿 Hot water",
        "household_voice_assistant": "🗣️ Voice assistant",
        "household_goal": "🎯 Main goal",
    }
    report = "👥 HOUSEHOLD PROFILE\n━━━━━━━━━━━━━━━━━━\n"
    for qid, label in labels.items():
        val = data.get(qid, "")
        if val and val != "n/a":
            report += f"  {label} : {val}\n"

    report += f"\n🧠 This profile feeds the skills :"
    if data.get("household_solar") == "yes":
        report += f"\n  ☀️ solar_window, solar recommendations"
    if data.get("household_heating") == "heat_pump":
        report += f"\n  🌡️ heat_pump_behavior, heat pump monitoring"
    if data.get("household_goal") == "reduce_bill":
        report += f"\n  💰 standby alerts enhanced, rate optimization"
    if data.get("household_voice_assistant") in ("google", "alexa"):
        report += f"\n  🗣️ recommendations to cut standby via {data['household_voice_assistant'].title()}"
    report += f"\n\n💡 Normal chat messages can add more home context."
    return report


def cmd_savings():
    """Detail of all the savings generated — the core metric."""
    conn = sqlite3.connect(DB_PATH)
    month = datetime.now().strftime("%Y-%m")

    # Total month
    total = conn.execute(
        "SELECT COALESCE(SUM(euros), 0), COALESCE(SUM(kwh_saved), 0), COUNT(*) "
        "FROM savings WHERE created_at LIKE ?", (f"{month}%",)
    ).fetchone()

    # By type
    by_type = conn.execute(
        "SELECT type, SUM(euros), SUM(kwh_saved), COUNT(*) "
        "FROM savings WHERE created_at LIKE ? GROUP BY type ORDER BY SUM(euros) DESC",
        (f"{month}%",)
    ).fetchall()

    # Par day (7 lasts days)
    by_day = conn.execute(
        "SELECT SUBSTR(created_at, 1, 10), SUM(euros), COUNT(*) "
        "FROM savings WHERE created_at >= ? GROUP BY SUBSTR(created_at, 1, 10) ORDER BY 1 DESC LIMIT 7",
        ((datetime.now() - timedelta(days=7)).isoformat(),)
    ).fetchall()

    tokens_row = conn.execute(
        "SELECT tokens_in, tokens_out FROM tokens WHERE month=?", (month,)
    ).fetchone()
    total_tokens = (tokens_row[0] + tokens_row[1]) if tokens_row else 0
    cost_tokens = round(total_tokens * 0.000001, 2)

    conn.close()

    report = f"💰 SAVINGS — {month}\n━━━━━━━━━━━━━━━━━━\n"

    type_labels = {
        "cycle_solar": "☀️ Cycles solar",
        "standby_killer": "🔇 Standby avoided",
        "rate_optimal": "⚡ Optimization rate",
        "surconsumption_evitee": "📉 Surconsumption evitee",
        "recommendation_applied": "💡 Recommendations",
    }

    report += f"\n📊 PAR SOURCE\n"
    for saving_type, euros, kwh, nb in by_type:
        label = type_labels.get(saving_type, saving_type)
        report += f"  {label}\n    +{euros:.2f}€ | {kwh:.1f} kWh | {nb} actions\n"

    report += f"\n📅 PAR JOUR (7 lasts)\n"
    for day, euros, nb in by_day:
        bar = "█" * min(20, int(euros * 20))
        report += f"  {day[5:]} : +{euros:.2f}€ {bar} ({nb})\n"

    report += f"\n━━━━━━━━━━━━━━━━━━"
    report += f"\n💰 Total month : {total[0]:.2f}€ | {total[1]:.1f} kWh | {total[2]} actions"
    report += f"\n🔑 Tokens : {cost_tokens:.2f}€"
    if cost_tokens > 0:
        roi = total[0] / cost_tokens
        report += f"\n📈 ROI: x{roi:.1f}"
        if roi >= 5:
            report += f" — each 1€ returns {roi:.0f}€"
    report += f"\n\n💡 The script saves money while your sleep."

    return report


def cmd_monitoring():
    """Complete view of everything the script monitors"""
    conn = sqlite3.connect(DB_PATH)
    states = ha_get("states") or []
    index = {e["entity_id"]: e for e in states}

    nb_ha = len(states)
    nb_carto = conn.execute("SELECT COUNT(*) FROM entity_map").fetchone()[0]
    appliance_count = conn.execute("SELECT COUNT(*) FROM appliances WHERE monitored=1").fetchone()[0]
    nb_ignores = conn.execute("SELECT COUNT(*) FROM appliances WHERE monitored=0").fetchone()[0]
    role_count = conn.execute("SELECT COUNT(*) FROM roles").fetchone()[0]
    baseline_count = conn.execute("SELECT COUNT(*) FROM baselines").fetchone()[0]
    expertise_count = conn.execute("SELECT COUNT(*) FROM expertise").fetchone()[0]

    categories = conn.execute(
        "SELECT category, COUNT(*) FROM entity_map GROUP BY category ORDER BY COUNT(*) DESC"
    ).fetchall()

    appliances = conn.execute(
        "SELECT entity_id, appliance_type, custom_name FROM appliances WHERE monitored=1"
    ).fetchall()

    conn.close()

    report = "🛡️ ACTIVE MONITORING\n━━━━━━━━━━━━━━━━━━\n"
    report += f"\n📡 Home Assistant : {nb_ha} entities detected"
    report += f"\n📊 Cartographie : {nb_carto} entities mapped"
    report += f"\n🎯 Roles: {role_count} auto-discovered"
    report += f"\n📈 Baselines: {baseline_count} learned behaviors"
    report += f"\n🧠 Expertise: {expertise_count}/50 rules"

    report += f"\n\n🔌 MONITORED APPLIANCES ({appliance_count})"
    if appliances:
        for eid, type_app, name in appliances:
            e = index.get(eid)
            state = ""
            if e:
                s = e.get("state", "?")
                u = e.get("attributes", {}).get("unit_of_measurement", "")
                if s not in ("unavailable", "unknown"):
                    state = f" — {s}{u}"
            icon = APPLIANCE_TYPES.get(type_app, "🔌")
            report += f"\n  {icon}{state}"
    else:
        report += "\n  (none yet)"

    if nb_ignores > 0:
        report += f"\n\n⬜ Bypassed/ignored: {nb_ignores} appliance(s) ignored"

    report += f"\n\n📋 CATEGORIES"
    for cat, nb in categories[:8]:
        report += f"\n  {cat} : {nb}"

    # Status of the threads
    now = datetime.now()
    _ts = lambda key: int((now - _watchdog.get(key, now)).total_seconds())
    report += f"\n\n⚙️ THREADS"
    report += f"\n  Monitoring : {_ts('monitoring_last_run')}s"
    report += f"\n  Plugs : {_ts('plugs_last_run')}s"
    report += f"\n  Polling : {_ts('polling_last_update')}s"

    has_cycle = any(v == "active" for v in _state_plugs.values())
    report += f"\n\n🎯 Mode : {'SNIPER 20s' if has_cycle else 'Standby 60s'}"

    return report


def cmd_commands():
    """Main menu with Telegram command buttons"""
    menus = {
        "⚡ Energy": [
            ("⚡ Energy", "/energy"),
            ("☀️ Solar", "/solar"),
            ("📈 ROI", "/roi"),
            ("⚡ Rate", "/rate"),
        ],
        "🏠 Home": [
            ("🔋 Batteries", "/batteries"),
            ("📡 Zigbee", "/zigbee"),
            ("💾 NAS", "/nas"),
            ("🌡️ Heating", "/heating"),
        ],
        "🔌 Machines": [
            ("🔌 Appliances", "/appliances"),
            ("🔄 Cycles", "/cycles"),
            ("📋 Programs", "/programs"),
            ("🛡️ Monitoring", "/monitoring"),
        ],
        "🧠 AI": [
            ("🧠 Intelligence", "/intelligence"),
            ("📊 Baselines", "/baselines"),
            ("📚 Expertise", "/expertise"),
            ("🎯 Roles", "/roles"),
        ],
        "📋 Outils": [
            ("📋 Audit", "/audit"),
            ("📅 Calendrier", "/calendar"),
            ("💰 Savings", "/savings"),
            ("📖 Help", "/help"),
        ],
    }

    for cat_name, cmds in menus.items():
        buttons = [{"text": label, "callback_data": f"cmd:{cmd.replace('/', '')}"} for label, cmd in cmds]
        telegram_send_buttons(cat_name, buttons)

    return ""


def cmd_appliances():
    """Show appliances and power consumers configured for monitoring."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT entity_id, appliance_type, custom_name, monitored FROM appliances ORDER BY appliance_type").fetchall()
    conn.close()
    if not rows:
        candidates = _collect_appliance_candidates()
        msg = "🔌 No appliance monitors configured yet."
        if candidates:
            msg += f"\n\nI can see {len(candidates)} power/energy sensor candidate(s) in Home Assistant."
        msg += "\n\nMention the appliance or power sensor in chat to start monitoring it."
        return msg

    CATEGORIES = {
        "cycles": {"label": "🔄 LARGE CONSUMERS (cycles)", "types": {"washing_machine", "dryer", "dishwasher", "freezer", "forr"}},
        "standby": {"label": "🔇 STANDBY CUTTERS", "types": {"standby_killer"}},
        "monitoring": {"label": "📊 ENERGY MONITORING", "types": {"energy_monitor"}},
        "other": {"label": "🔌 OTHER", "types": {"other"}},
        "ignore": {"label": "⬜ SKIPPED", "types": {"ignore"}},
    }

    report = "🔌 APPLIANCES AND POWER CONSUMERS\n━━━━━━━━━━━━━━━━━━\n"
    for cat_key, cat_info in CATEGORIES.items():
        cat_rows = [r for r in rows if r[1] in cat_info["types"]]
        if cat_rows:
            report += f"\n{cat_info['label']}\n"
            for eid, type_app, name, monitored in cat_rows:
                report += f"  {'✅' if monitored else '⬜'} {name or APPLIANCE_TYPES.get(type_app, type_app)}\n"

    report += f"\n💡 Mention appliance changes in chat to update monitoring."
    return report


def cmd_programs():
    """Show learned cycle profiles for each appliance, using factual data only."""
    data, nb = skill_get("cycle_signatures")
    if not data:
        return "🔄 No cycles recorded — profiles are learned automatically after each cycle."

    report = f"🔄 LEARNED CYCLE PROFILES\n━━━━━━━━━━━━━━━━━━\n"

    for eid, info in data.items():
        name = info.get("name", eid)
        nb_total = info.get("nb_cycles_total", info.get("nb_cycles", 0))
        report += f"\n🔌 {name} ({nb_total} cycles)\n"

        progs = info.get("programs", {})
        if progs:
            for prog_name, p in sorted(progs.items(), key=lambda x: -x[1].get("nb_cycles", 0)):
                duration = p.get("duration_avg", 0)
                consumption = p.get("consumption_avg", 0)
                average_power = p.get("power_avg", 0)
                nb_p = p.get("nb_cycles", 0)
                sig = p.get("signature", "?")
                price = rate_current_kwh_price()
                cost = consumption * price
                report += f"  📊 {prog_name} ({nb_p}x)\n"
                report += f"    {duration:.0f} min | {consumption:.2f} kWh | ~{average_power:.0f}W avg | {cost:.2f}\n"
                report += f"    Signature: {sig}\n"
        else:
            duration = info.get("duration_avg", 0)
            consumption = info.get("consumption_avg", 0)
            report += f"  {duration:.0f} min | {consumption:.2f} kWh\n"

    report += f"\n📊 {nb} total cycles analyzed"
    report += f"\n💡 Costs calculated at current rate ({rate_current_kwh_price():.4f}/kWh)"
    return report


def cmd_cycles():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        '''SELECT friendly_name, started_at, duration_min, consumption_kwh, cost_eur, program
           FROM appliance_cycles WHERE ended_at IS NOT NULL
           ORDER BY created_at DESC LIMIT 10'''
    ).fetchall()
    conn.close()
    if not rows:
        return "📊 No cycles recorded"
    report = "📊 RECENT CYCLES\n━━━━━━━━━━━━━━━━\n"
    for row in rows:
        fname, started_at, duration, consumption, cost = row[:5]
        prog = row[5] if len(row) > 5 else None
        date = started_at[:16].replace("T", " ") if started_at else "?"
        report += f"  {fname} — {date}\n    {duration} min | {consumption:.2f} kWh | {cost:.2f}"
        if prog:
            report += f"\n    🔍 {prog}"
        report += "\n"
    return report


def cmd_documentation():
    doc = f"""📖 HOME ASSISTANT AI COMPANION v{VERSION}

Available commands:
━━━━━━━━━━━━━━━━━━━━
/audit          → Home Assistant state and AI analysis
/energy         → Energy, solar, heat pump, thermostats, and weather
/solar          → Solar production and battery systems
/batteries      → Device batteries
/zigbee         → Zigbee network and LQI
/nas            → NAS monitoring
/automations    → Home Assistant automations
/addons         → HA Apps
/cycles         → Appliance cycle history
/budget         → AI token and cost usage
/rate           → Electricity rate status
/appliances     → Appliance and power-consumer status
/profile        → Home context remembered from chat
/scan           → Rescan and learn entities
/debug          → Internal diagnostic state
/logs           → Last 20 log lines
/memory_store   → What the AI has memorized
/documentation  → This help menu
/export         → Export assistant.py
/script         → Export assistant.py
/ai             → Execute autonomous AI helper

Free-form chat → Ask for Home Assistant actions, monitoring, or analysis."""
    return doc


def cmd_problem(description):
    """Auto-correction: read the script, ask the configured strong model for a patch, apply it, restart."""
    telegram_send(f"🔧 AUTO-CORRECTION\nIssue: {description}\n\nAnalyzing...")

    # 1. Read the script through the local deploy server.
    try:
        req_read = urllib.request.Request("http://localhost:8501/read")
        cfg_secret = CFG.get("deploy_secret", "")
        req_read.add_header("Authorization", f"Bearer {cfg_secret}")
        resp_read = urllib.request.urlopen(req_read, timeout=15)
        script_data = json.loads(resp_read.read().decode())
        script_code = script_data["content"]
        script_lines = script_data["lines"]
        telegram_send(f"📄 Script read : {script_lines} lines")
    except Exception as e:
        return f"❌ Unable to read the script: {e}"

    # 2. Build the prompt for the configured strong model
    system_prompt = (
        "You are an expert Python developer specialized in Home Assistant home automation.\n"
        "The user reports a problem in assistant.py.\n"
        "Analyze the code and propose one focused patch.\n\n"
        "Strict rules:\n"
        "- Reply ONLY in valid JSON, nothing else\n"
        "- Format: {\"old_str\": \"code_to_replace\", \"new_str\": \"new_code\", \"explanation\": \"what you are fixing\"}\n"
        "- old_str must exactly match the current code (escapes, indentation, quotes)\n"
        "- old_str must appear ONLY ONCE in the script\n"
        "- Change only the minimum necessary code\n"
        "- No markdown, no ```json, only the JSON raw\n"
    )

    user_prompt = (
        f"PROBLEM SIGNALE :\n{description}\n\n"
        f"SCRIPT COMPLET ({script_lines} lines) :\n{script_code}"
    )

    # 3. Calledr LLM
    try:
        blocks, t_in, t_out = llm_provider.llm_completion(
            CFG, [{"role": "user", "content": user_prompt}],
            model=llm_provider.get_model(CFG, use_strong=True),
            max_tokens=4000,
            system_prompt=system_prompt
        )
        response_raw = llm_provider.stream_text(blocks).strip()
        log_token_usage(t_in, t_out)
        provider_name = CFG.get("llm_provider", "anthropic")
        telegram_send(f"🤖 {provider_name} replied ({t_out} tokens)")
    except Exception as e:
        return f"❌ AI provider error: {e}"

    # 4. Parser the JSON
    try:
        text_json = response_raw.replace("```json", "").replace("```", "").strip()
        patch_data = json.loads(text_json)
        old_code = patch_data.get("old_str", "")
        new_code = patch_data.get("new_str", "")
        explanation = patch_data.get("explanation", "no explanation")
    except Exception as e:
        telegram_send(f"❌ Unparseable AI response:\n{response_raw[:500]}")
        return f"❌ Invalid JSON : {e}"

    if not old_code:
        return f"❌ Empty patch — the AI model found no fix.\nExplanation: {explanation}"

    count = script_code.count(old_code)
    if count == 0:
        telegram_send(f"❌ old_str not found in script\nAI explanation: {explanation}")
        return "❌ Patch not applicable — old_str missing of the script"
    if count > 1:
        telegram_send(f"❌ old_str found {count} times — ambiguous")
        return "❌ Ambiguors patch — old_str found multiple times"

    telegram_send(
        f"📋 PATCH PROPOSE\n━━━━━━━━━━━━━━\n"
        f"Explication : {explanation}\n\n"
        f"Ancien ({len(old_code)} chars) :\n{old_code[:300]}...\n\n"
        f"New ({len(new_code)} chars) :\n{new_code[:300]}..."
    )
    telegram_send_buttons(
        "Apply this patch ?",
        [
            {"text": "✅ Apply + restart", "callback_data": "patch_apply:auto"},
            {"text": "❌ Cancel", "callback_data": "patch_cancel:auto"},
        ]
    )

    mem_set("patch_pending_old", old_code)
    mem_set("patch_pending_new", new_code)
    mem_set("patch_pending_expl", explanation)

    return f"🔧 Patch pending validation"


def cmd_clean_carto():
    """Cleans the energy entity map and removes noisy entries"""
    conn = sqlite3.connect(DB_PATH)
    modifications = 0

    # ecu_inverters, ecu_inverters_online, inverter_*_temperature
    # Tort the remaining (frequency, voltage, signal, binary_sensor, switch, update) → ignore
    rows = conn.execute(
        "SELECT entity_id FROM entity_map WHERE category='energy_solar'"
    ).fetchall()

    useful_aps = set()
    for r_solar in ["solar_production_w", "solar_production_kwh", "solar_production_lifetime", "inverters_total", "inverters_online"]:
        eid_r = role_get(r_solar)
        if eid_r:
            useful_aps.add(eid_r)
    if not useful_aps:
        useful_aps = {"sensor.ecu_current_power", "sensor.ecu_today_energy",
                      "sensor.ecu_lifetime_energy", "sensor.ecu_inverters",
                      "sensor.ecu_inverters_online"}

    for (eid,) in rows:
        eid_low = eid.lower()

        # Keep essential ECU sensors.
        if eid in useful_aps:
            continue

        if "inverter_" in eid_low and "_temperature" in eid_low:
            continue

        if "solarbank_e1600" in eid_low or "system_anker" in eid_low or "mi80_microinverter" in eid_low:
            # SOC / battery
            if any(k in eid_low for k in ["state_of_charge", "state_of_charge", "battery", "battery",
                                           "caheat_pumpite", "reserve_soc", "charge"]):
                conn.execute("UPDATE entity_map SET category='energy_battery' WHERE entity_id=?", (eid,))
                modifications += 1
            # Production / power solar
            elif any(k in eid_low for k in ["power_solar", "solar_power", "energy_solar_sb",
                                             "dc_output", "home_power", "system_output",
                                             "discharge", "alimentation_ac"]):
                conn.execute("UPDATE entity_map SET category='energy_production' WHERE entity_id=?", (eid,))
                modifications += 1
            # Config / info → ignore
            elif any(k in eid_low for k in ["firmware", "clord", "code_d_error", "informations",
                                             "mise_a_day", "devise", "price", "type_de_price",
                                             "solar_banks", "inverter", "data_time",
                                             "savings", "rendement", "actualiser",
                                             "autoriser_l_exportation", "discharge_prioritaire",
                                             "automatic_update", "administration",
                                             "ota", "mode"]):
                conn.execute("UPDATE entity_map SET category='ignore' WHERE entity_id=?", (eid,))
                modifications += 1
            else:
                conn.execute("UPDATE entity_map SET category='ignore' WHERE entity_id=?", (eid,))
                modifications += 1
            continue

        # Individual inverters with frequency, voltage, or signal sensors are ignored.
        if "inverter_" in eid_low and any(k in eid_low for k in ["frequency", "voltage", "signal"]):
            conn.execute("UPDATE entity_map SET category='ignore' WHERE entity_id=?", (eid,))
            modifications += 1
            continue

        # binary_sensor, switch, button, update, automation in energy_solar → ignore
        domain = eid.split(".")[0]
        if domain in ("binary_sensor", "switch", "button", "update", "automation", "select", "number"):
            conn.execute("UPDATE entity_map SET category='ignore' WHERE entity_id=?", (eid,))
            modifications += 1
            continue

        # Ecojoko surplus → energy_consumption
        if "ecojoko" in eid_low and "surplus" in eid_low:
            conn.execute("UPDATE entity_map SET category='energy_consumption' WHERE entity_id=?", (eid,))
            modifications += 1
            continue

        if "energy_current_hour" in eid_low or "energy_next_hour" in eid_low:
            conn.execute("UPDATE entity_map SET category='energy_forecast' WHERE entity_id=?", (eid,))
            modifications += 1
            continue

    conn.execute(
        "UPDATE entity_map SET category='ignore' WHERE entity_id='sensor.22081212ug_charger_type'"
    )

    conn.commit()

    # Report
    counts = {}
    for (cat, nb) in conn.execute(
        "SELECT category, COUNT(*) FROM entity_map WHERE category LIKE 'energy%' GROUP BY category"
    ).fetchall():
        counts[cat] = nb
    conn.close()

    report = f"🧹 MAP CLEANUP\n━━━━━━━━━━━━━━━━━━\n"
    report += f"Modifications : {modifications}\n\n"
    for cat, nb in sorted(counts.items()):
        report += f"  [{cat}]: {nb} entities\n"
    report += f"\n✅ Done — /diag_carto to verify"
    return report


def cmd_test_weather():
    """Test weather monitoring without the usual day/hour filter."""
    states = ha_get("states")
    if not states:
        return "❌ HA unreachable"
    index = {e["entity_id"]: e for e in states}
    report = "🧪 WEATHER MONITORING TEST\n━━━━━━━━━━━━━━━━━━\n"

    alert_93 = index.get(role_get("weather_alert") or "sensor.weather_alert")
    if alert_93:
        attrs = alert_93.get("attributes", {})
        report += f"\n📊 Weather alert: {alert_93['state']}\n"
        LEVELS = {"Green": 0, "Yellow": 1, "Orange": 2, "Red": 3}
        for risk in ["violent wind", "rain flooding", "storms", "snow ice", "extreme cold", "flooding"]:
            color = attrs.get(risk, "Green")
            level = LEVELS.get(color, 0)
            if level == 0:
                icon = "🟢"
            elif level == 1:
                icon = "🟡"
            elif level == 2:
                icon = "🟠"
            else:
                icon = "🔴"
            report += f"  {icon} {risk} : {color}\n"
    else:
        report += "\n❌ sensor.weather_alert not found\n"

    # 2. Rain in the next hour
    rain = index.get(role_get("weather_next_rain") or "sensor.weather_next_rain")
    if rain:
        rain_attrs = rain.get("attributes", {})
        forecast_1h = rain_attrs.get("1_hour_forecast", {})
        report += f"\n🌧️ Rain in the next hour :\n"
        if isinstance(forecast_1h, dict):
            rain_expected = [t for t, v in forecast_1h.items() if "rain" in v.lower()]
            dry_slots = [t for t, v in forecast_1h.items() if "dry" in v.lower()]
            if rain_expected:
                report += f"  🌧️ Rain expected: {', '.join(rain_expected)}\n"
            else:
                report += f"  ✅ Dry weather ({len(dry_slots)} slots)\n"
        else:
            report += f"  state: {rain['state']}\n"

    # 3. Gusts
    weather = index.get("weather.home")
    if weather:
        w_attrs = weather.get("attributes", {})
        wind = w_attrs.get("wind_speed", 0)
        gusts = w_attrs.get("wind_gust_speed", 0)
        report += f"\n💨 Wind : {wind} km/h | Gusts : {gusts} km/h\n"
        try:
            r_val = float(gusts)
            if r_val >= 80:
                report += f"  🔴 STRONG GUSTS → alert triggered\n"
            elif r_val >= 60:
                report += f"  🟡 Moderate gusts → alert triggered\n"
            else:
                report += f"  ✅ Below threshold (60 km/h)\n"
        except Exception:
            pass

    # 4. Snow
    snow = index.get(role_get("weather_snow_chance") or "sensor.weather_snow_chance")
    if snow:
        report += f"\n❄️ Snow risk: {snow['state']}%\n"

    # 5. Rain probability
    rain_chance = index.get(role_get("weather_rain_chance") or "sensor.weather_rain_chance")
    if rain_chance:
        report += f"🌧️ Rain risk: {rain_chance['state']}%\n"

    report += "\n📅 FORECAST FOR UPCOMING DAYS :\n"
    forecast = ha_get_forecast("weather.home", "daily")
    if forecast:
        if True:
            for prev in forecast[:3]:
                dt = prev.get("datetime", "?")[:10]
                cond = prev.get("condition", "?")
                precip = prev.get("precipitation", 0) or 0
                precip_prob = prev.get("precipitation_probability", 0) or 0
                temp_max = prev.get("temperature", "?")
                temp_min = prev.get("templow", "?")
                wind = prev.get("wind_speed", 0) or 0
                day_alerts = []
                try:
                    if float(precip) >= 10:
                        day_alerts.append("🌧️ HEAVY RAIN")
                    elif float(precip) >= 5:
                        day_alerts.append("🌧️ Rain")
                except Exception:
                    pass
                try:
                    if float(wind) >= 50:
                        day_alerts.append("💨 STRONG WIND")
                except Exception:
                    pass
                try:
                    if temp_min != "?" and float(temp_min) <= 0:
                        day_alerts.append("🥶 FREEZE")
                except Exception:
                    pass
                if cond in ("snowy", "snowy-rainy"):
                    day_alerts.append("❄️ SNOW")
                flag = " ← ⚠️" if day_alerts else ""
                report += f"  {dt} : {cond} | {temp_min}→{temp_max}°C | {precip}mm ({int(float(precip_prob))}%) | {int(float(wind))} km/h{flag}\n"
                if day_alerts:
                    report += f"    {' | '.join(day_alerts)}\n"
    else:
        report += "  ⚠️ No forecast available — check weather integration\n"

    # 7. Force a test alert send
    report += "\n━━━━━━━━━━━━━━━━━━\n🧪 Send test alert...\n"
    _alert_if_new(
        "weather_test_alert",
        "🧪 WEATHER ALERT TEST\n"
        "This is a weather monitoring test.\n"
        "Real alerts will work the same way.\n"
        "✅ System operational",
        delay_h=0
    )
    report += "✅ Test alert sent"

    return report


def cmd_diag_forecast():
    """Debug all available forecast retrieval paths."""
    report = "🔧 DIAG FORECAST\n━━━━━━━━━━━━━━\n"

    # Test 1 : attributes weather entity
    report += "\n[1] Attributes weather.home :\n"
    e = ha_get_state("weather.home")
    if e:
        attrs = e.get("attributes", {})
        forecast = attrs.get("forecast", None)
        if forecast:
            report += f"  ✅ forecast in attributes: {len(forecast)} entries\n"
            report += f"  First : {json.dumps(forecast[0])[:200]}\n"
        else:
            report += f"  ❌ No forecast in attributes\n"
            report += f"  Available keys: {', '.join(list(attrs.keys())[:15])}\n"

    # Test 2 : service weather.get_forecasts daily
    report += "\n[2] Service weather.get_forecasts (daily) :\n"
    try:
        result = ha_post("services/weather/get_forecasts", {
            "entity_id": "weather.home",
            "type": "daily"
        })
        report += f"  Type return : {type(result).__name__}\n"
        report += f"  Content : {json.dumps(result, ensure_ascii=False)[:500]}\n"
    except Exception as ex:
        report += f"  ❌ Error : {ex}\n"

    # Test 3 : service weather.get_forecasts hourly
    report += "\n[3] Service weather.get_forecasts (hourly) :\n"
    try:
        result2 = ha_post("services/weather/get_forecasts", {
            "entity_id": "weather.home",
            "type": "hourly"
        })
        report += f"  Type return : {type(result2).__name__}\n"
        if result2 and isinstance(result2, dict):
            for k, v in result2.items():
                if isinstance(v, dict) and "forecast" in v:
                    fc = v["forecast"]
                    report += f"  ✅ Key '{k}' → {len(fc)} entries\n"
                    if fc:
                        report += f"  First : {json.dumps(fc[0], ensure_ascii=False)[:200]}\n"
                elif isinstance(v, list):
                    report += f"  Key '{k}' → list {len(v)} items\n"
                else:
                    report += f"  Key '{k}' → {str(v)[:100]}\n"
        else:
            report += f"  Raw : {str(result2)[:300]}\n"
    except Exception as ex:
        report += f"  ❌ Error : {ex}\n"

    # Test 4 : endpoint calendar-like
    report += "\n[4] ha_get_forecast() :\n"
    fc = ha_get_forecast()
    report += f"  Result: {len(fc)} entries\n"
    if fc:
        report += f"  First : {json.dumps(fc[0], ensure_ascii=False)[:200]}\n"

    return report


def cmd_diag_weather():
    """List weather-related Home Assistant entities."""
    states = ha_get("states")
    if not states:
        return "❌ HA unreachable"
    report = "🌦️ WEATHER DIAG\n━━━━━━━━━━━━━━━━━━\n"
    keywords = [
        "weather", "alert", "rain", "wind", "snow", "storm", "flood",
        "ice", "temperature", "humidity", "precipitation",
    ]
    found = []
    for e in states:
        eid = e["entity_id"].lower()
        fname = e.get("attributes", {}).get("friendly_name", "").lower()
        combined = eid + " " + fname
        if any(k in combined for k in keywords):
            attrs = e.get("attributes", {})
            report += f"\n{e['entity_id']}\n"
            report += f"  state: {e['state']}\n"
            report += f"  name: {attrs.get('friendly_name', '')}\n"
            for k, v in attrs.items():
                if k not in ("friendly_name", "icon", "entity_picture"):
                    report += f"  {k}: {str(v)[:100]}\n"
            found.append(e["entity_id"])
    report += f"\n━━━━━━━━━━━━━━━━━━\nTotal: {len(found)} entities"
    return report


def cmd_learning():
    """Shows the learning daynal: failures, successes, lessons"""
    conn = sqlite3.connect(DB_PATH)

    # Stats globales
    total = conn.execute("SELECT COUNT(*) FROM decisions_log").fetchone()[0]
    failures = conn.execute("SELECT COUNT(*) FROM decisions_log WHERE success=0").fetchone()[0]
    success = conn.execute("SELECT COUNT(*) FROM decisions_log WHERE success=1").fetchone()[0]
    expertise_count = conn.execute("SELECT COUNT(*) FROM expertise").fetchone()[0]

    report = f"📕 CONTINUOUS LEARNING\n━━━━━━━━━━━━━━━━━━\n"
    report += f"Decisions tracked: {total}\n"
    report += f"Failures: {failures} | Successes: {success}\n"
    report += f"Expertise: {expertise_count} rules learned\n"

    if failures + success > 0:
        rate = success / (failures + success) * 100
        if rate >= 90:
            report += f"Success rate: {rate:.0f}% 🟢\n"
        elif rate >= 70:
            report += f"Success rate: {rate:.0f}% 🟡\n"
        else:
            report += f"Success rate: {rate:.0f}% 🔴\n"

    lasts_errors = conn.execute(
        "SELECT action, result, created_at FROM decisions_log "
        "WHERE success=0 ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    if lasts_errors:
        report += "\n❌ LATEST FAILURES :\n"
        for action, res, date in lasts_errors:
            report += f"  [{date[:16]}] {action}\n    {res[:80]}\n"

    recurring = conn.execute(
        "SELECT action, COUNT(*) as nb FROM decisions_log "
        "WHERE success=0 AND created_at > datetime('now', '-7 days') "
        "GROUP BY action HAVING nb >= 2 ORDER BY nb DESC LIMIT 5"
    ).fetchall()
    if recurring:
        report += "\n🔁 RECURRING FAILURES (7j) :\n"
        for action, nb in recurring:
            report += f"  {action} : {nb} times\n"

    recentes = conn.execute(
        "SELECT category, insight, confidence FROM expertise "
        "ORDER BY updated_at DESC LIMIT 5"
    ).fetchall()
    if recentes:
        report += "\n📚 LATEST LESSONS :\n"
        for cat, ins, conf in recentes:
            stars = "★" * min(5, int(conf * 5))
            report += f"  {stars} [{cat}] {ins}\n"

    conn.close()
    return report


def cmd_expertise():
    """Shows the accumulated AI expertise"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT category, insight, confidence, nb_validations, created_at FROM expertise "
        "ORDER BY confidence DESC"
    ).fetchall()
    nb_decisions = conn.execute("SELECT COUNT(*) FROM decisions_log").fetchone()[0]
    conn.close()

    report = f"📚 AI EXPERTISE — {len(rows)} rules learned\n━━━━━━━━━━━━━━━━━━\n"

    if not rows:
        report += "No expertise yet - run /analysis to begin.\n"
        report += "Expertise is built automatically every 6h."
        return report

    categories = {}
    for cat, insight, conf, nb_val, created in rows:
        if cat not in categories:
            categories[cat] = []
        categories[cat].append((insight, conf, nb_val, created))

    for cat, insights in sorted(categories.items()):
        report += f"\n[{cat.upper()}]\n"
        for insight, conf, nb_val, created in insights:
            stars = "★" * min(5, int(conf * 5)) + "☆" * (5 - min(5, int(conf * 5)))
            report += f"  {stars} {insight}\n"
            report += f"    Confidence {conf:.0%} | {nb_val} validation(s) | since {created[:10]}\n"

    report += f"\n📊 {nb_decisions} decisions tracked\n"
    report += "\n💡 Confidence: ★☆☆☆☆ = new | ★★★★★ = validated by data"

    return report


def cmd_analysis():
    """Dekey_namenche a analysis AI immediate on the data accumulateds"""
    telegram_send("🧠 Analysis in progress — the AI model is examining your data...")
    states = ha_get("states")
    if not states:
        return "❌ HA unreachable"
    index = {e["entity_id"]: e for e in states}
    now = datetime.now()
    try:
        _analysis_ia_periodique(states, index, now)
        return ""  # The message is sent by _analysis_ia_periodique
    except Exception as e:
        return f"❌ Error analysis : {e}"


def cmd_diag_hc():
    """Search Home Assistant for off-peak hour sources."""
    states = ha_get("states")
    if not states:
        return "❌ HA unreachable"
    report = "🔧 SEARCH OFF-PEAK HOURS\n━━━━━━━━━━━━━━━━━━\n"
    found = []

    for e in states:
        eid = e["entity_id"]
        attrs = e.get("attributes", {})
        fname = attrs.get("friendly_name", "")

        # Search in the entity_id
        eid_low = eid.lower()
        if any(k in eid_low for k in ["off_peak", "off_peak", "offpeak", "hc_hp", "hp_hc", "rate", "tariff", "price"]):
            report += f"\n📌 {eid}\n  {fname} = {e['state']}\n"
            for k, v in attrs.items():
                if isinstance(v, str) and len(v) < 200:
                    report += f"  {k}: {v}\n"
                elif isinstance(v, (int, float, bool)):
                    report += f"  {k}: {v}\n"
            found.append(eid)

        # Search in the attributes
        for k, v in attrs.items():
            k_low = str(k).lower()
            v_str = str(v).lower()
            if any(kw in k_low for kw in ["off_peak", "off_peak", "offpeak", "hc", "current_rate"]):
                if eid not in found:
                    report += f"\n📌 {eid} (attr: {k})\n  {fname} = {e['state']}\n  {k}: {v}\n"
                    found.append(eid)
            if any(kw in v_str for kw in ["22:00", "23:00", "06:00", "off peak", "off peak"]):
                if eid not in found:
                    report += f"\n📌 {eid} (value contains off-peak data)\n  {fname} = {e['state']}\n  {k}: {v}\n"
                    found.append(eid)

    if not found:
        report += "\nNo off-peak entity found in Home Assistant."

    return report


def cmd_diag_plugs():
    """Diagnostic plugs — shows exactly what monitoring_plugs monitors"""
    states = ha_get("states")
    if not states:
        return "❌ HA unreachable"
    index = {e["entity_id"]: e for e in states}

    report = "🔧 DIAG PLUGS\n━━━━━━━━━━━━━━\n"

    # 1. Ce that the entity_map dit
    plugs = entity_map_get_by_category("connected_plug")
    report += f"\nMapping connected_plug: {len(plugs)} entities\n"

    plugs_w = []
    plugs_no_w = []
    for eid, sc, pc in plugs:
        if not eid.startswith("sensor."):
            continue
        e = index.get(eid)
        if not e:
            report += f"  ❌ {eid} — ABSENT of HA\n"
            continue
        unit = e.get("attributes", {}).get("unit_of_measurement", "")
        state = e["state"]
        fname = e.get("attributes", {}).get("friendly_name", eid)
        if unit in ("W", "w", "Watt"):
            plugs_w.append((eid, fname, state, unit))
        else:
            plugs_no_w.append((eid, fname, state, unit))

    report += f"\n✅ MONITORED (sensor.* + W unit): {len(plugs_w)}\n"
    for eid, fname, state, unit in plugs_w:
        threshold = "🟢 >200W" if state not in ("unavailable", "unknown") and float(state) > 200 else "⚫"
        report += f"  {threshold} {fname} = {state} {unit}\n    {eid}\n"

    if plugs_no_w:
        report += f"\n⚠️ IGNORED (no W unit): {len(plugs_no_w)}\n"
        for eid, fname, state, unit in plugs_no_w:
            report += f"  {fname} = {state} {unit}\n    {eid}\n"

    report += "\n🔍 UNCATEGORIZED POWER :\n"
    for e in states:
        eid = e["entity_id"]
        if not eid.startswith("sensor."):
            continue
        unit = e.get("attributes", {}).get("unit_of_measurement", "")
        if unit not in ("W", "w", "Watt"):
            continue
        carto = entity_map_get(eid)
        if carto and carto[0] == "connected_plug":
            continue  # Deja categorise
        fname = e.get("attributes", {}).get("friendly_name", eid)
        if any(k in eid.lower() for k in ["power", "power", "watt"]):
            cat = carto[0] if carto else "UNCATEGORIZED"
            report += f"  ⚠️ {fname} = {e['state']} W | carto: {cat}\n    {eid}\n"

    return report


def cmd_diag_ecojoko():
    """Diagnostic Ecojoko — all the entities with their value_texts"""
    states = ha_get("states")
    if not states:
        return "❌ HA unreachable"
    report = "🔧 DIAG ECOJOKO\n━━━━━━━━━━━━━━\n"
    for e in states:
        eid = e["entity_id"].lower()
        fname = e.get("attributes", {}).get("friendly_name", "")
        if "ecojoko" in eid or "ecojoko" in fname.lower():
            unit = e.get("attributes", {}).get("unit_of_measurement", "")
            dc = e.get("attributes", {}).get("device_class", "")
            state = e["state"]
            icon = "❌" if state in ("unavailable", "unknown") else "✅"
            report += f"  {icon} {e['entity_id']}\n    {fname} = {state} {unit} (dc:{dc})\n"
    return report


def cmd_diag_nas():
    """Diagnostic NAS — displays all the entities NAS with their state"""
    states = ha_get("states")
    if not states:
        return "❌ HA unreachable"
    index = {e["entity_id"]: e for e in states}
    report = "🔧 DIAG NAS\n━━━━━━━━━━━━━━\n"

    for e in states:
        eid = e["entity_id"].lower()
        fname = e.get("attributes", {}).get("friendly_name", "")
        if any(k in eid or k in fname.lower() for k in ["synology", "syno2", "syno_", "nas_"]):
            domain = e["entity_id"].split(".")[0]
            unit = e.get("attributes", {}).get("unit_of_measurement", "")
            dc = e.get("attributes", {}).get("device_class", "")
            state = e["state"]
            icon = "❌" if state in ("unavailable", "unknown") else "✅"
            report += f"  {icon} [{domain}] {e['entity_id']}\n"
            report += f"    {fname} = {state} {unit} (dc:{dc})\n"

    return report


def cmd_diag_carto():
    """Diagnostic entity_map — list all the entities by category energy"""
    conn = sqlite3.connect(DB_PATH)
    report = "🔧 MAP DIAG\n━━━━━━━━━━━━━━━━━━\n"
    for cat in ["energy_solar", "energy_heating", "energy_consumption", "energy_battery", "energy_production", "energy_forecast"]:
        rows = conn.execute(
            "SELECT entity_id, subcategory, friendly_name FROM entity_map WHERE category=? ORDER BY entity_id",
            (cat,)
        ).fetchall()
        report += f"\n[{cat}] ({len(rows)} entities)\n"
        for eid, sc, fn in rows:
            report += f"  {fn or eid} | {sc}\n"
    conn.close()
    return report


def cmd_diag_energy():
    """Diagnostic energy — displays entity_map + states HA for debug"""
    states = ha_get("states")
    if not states:
        return "❌ HA unreachable"
    index = {e["entity_id"]: e for e in states}
    report = "🔧 DIAG ENERGY\n━━━━━━━━━━━━━━\n"

    # 1. Micro-inverter APSystems
    report += "\n📡 APSYSTEMS (HA entities):\n"
    for e in states:
        eid = e["entity_id"]
        if "apsystem" in eid.lower() or "microinverter" in eid.lower() or "micro_inverter" in eid.lower():
            state = e["state"]
            unit = e.get("attributes", {}).get("unit_of_measurement", "")
            icon = "❌" if state in ("unavailable", "unknown") else "✅"
            report += f"  {icon} {eid} = {state} {unit}\n"

    # 2. heat pump / climate
    report += "\n🌡️ heat pump / CLIMATE :\n"
    for e in states:
        if e["entity_id"].startswith("climate."):
            carto = entity_map_get(e["entity_id"])
            cat_str = carto[0] if carto else "UNMAPPED"
            report += f"  {e['entity_id']} = {e['state']} | carto: {cat_str}\n"

    report += "\n🔌 CONNECTED PLUGS (entity_map) :\n"
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT entity_id, subcategory, room, friendly_name FROM entity_map WHERE category='connected_plug'"
    ).fetchall()
    conn.close()
    for eid, sc, pc, fn in rows:
        e = index.get(eid)
        if e:
            state = e["state"]
            unit = e.get("attributes", {}).get("unit_of_measurement", "")
            report += f"  {fn or eid} [{sc}] = {state} {unit}\n"
        else:
            report += f"  ❌ {fn or eid} — missing from HA\n"

    report += "\n🔍 SEARCH DRYER :\n"
    for e in states:
        eid = e["entity_id"].lower()
        fn = e.get("attributes", {}).get("friendly_name", "").lower()
        if any(k in eid or k in fn for k in ["dryer", "dryer", "dryer", "dryer"]):
            carto = entity_map_get(e["entity_id"])
            cat_str = carto[0] if carto else "UNMAPPED"
            report += f"  {e['entity_id']} = {e['state']} | carto: {cat_str}\n"

    report += "\n📊 ENERGY MAP :\n"
    conn = sqlite3.connect(DB_PATH)
    for cat in ["energy_solar", "energy_heating", "energy_consumption", "energy_battery", "energy_production"]:
        rows = conn.execute(
            "SELECT entity_id FROM entity_map WHERE category=?", (cat,)
        ).fetchall()
        report += f"  [{cat}]: {len(rows)} entities\n"
        for (eid,) in rows[:5]:
            e = index.get(eid)
            state = e["state"] if e else "ABSENT HA"
            icon = "❌" if state in ("unavailable", "unknown", "ABSENT HA") else "✅"
            report += f"    {icon} {eid} = {state}\n"
    conn.close()

    return report


def cmd_script_export():
    """Exports the SCRIPT assistant.py via Telegram"""
    try:
        with open(os.path.join(BASE_DIR, "assistant.py"), "r") as f:
            script = f.read()
        
        for i in range(0, len(script), 3500):
            telegram_send(script[i:i+3500])
            time.sleep(0.2)
        
        return "✅ Script exported in chunks"
    except Exception as e:
        return f"❌ Error export: {e}"


def cmd_watches():
    """List active dynamic alerts."""
    try:
        conn = sqlite3.connect(DB_PATH)
        watches = conn.execute("SELECT id, entity_pattern, condition, state_value, message, cooldown_min, last_triggered, active FROM watches ORDER BY id").fetchall()
        conn.close()
    except Exception as e:
        return f"❌ Error: {e}"

    if not watches:
        return "📭 No alerts configured.\nAsk me in natural language, e.g.: \"Alert me if an inverter goes offline\""

    lines = ["🔔 DYNAMIC ALERTS", "━━━━━━━━━━━━━━━━━━"]
    for wid, pattern, cond, val, msg, cooldown, last, active in watches:
        status = "🟢" if active else "🔴"
        desc = f"{status} #{wid} — {pattern}"
        desc += f"\n   Condition: {cond}"
        if val:
            desc += f" {val}"
        desc += f"\n   Message: {msg[:60]}"
        desc += f"\n   Cooldown: {cooldown}min"
        if last:
            desc += f"\n   Last: {last[:16]}"
        lines.append(desc)
    return "\n".join(lines)



# =============================================================================
# =============================================================================

def _alert_night_ghost_consumption(index, now):
    """Detects an consumption abnormale entre 1h-5h. CRASH-PROOF."""
    try:
        if not (1 <= now.hour < 5):
            return
        eid_consumption = role_get("realtime_consumption")
        if not eid_consumption or eid_consumption not in index:
            return
        try:
            consumption_w = float(index[eid_consumption]["state"])
        except (ValueError, TypeError):
            return
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT AVG(avg_value) FROM baselines WHERE entity_id=? AND hour BETWEEN 1 AND 4 AND sample_count >= 5",
            (eid_consumption,)
        ).fetchone()
        conn.close()
        baseline = rows[0] if rows and rows[0] else 200
        threshold = baseline + 150
        if consumption_w > threshold:
            _alert_if_new(
                "ghost_consumption_nuit",
                f"👻 ABNORMAL NIGHT CONSUMPTION\n━━━━━━━━━━━━━━━━━━\n"
                f"Il is {now.strftime('%H:%M')} — grid consumption: {consumption_w:.0f}W\n"
                f"Habituellement : ~{baseline:.0f}W\nSurplus : +{consumption_w - baseline:.0f}W\n\n"
                f"Something may have been left on.",
                delay_h=6
            )
    except Exception as e:
        log.debug(f"Ghost consumption: {e}")


def _alert_freezer_outage(index, now):
    """Alert after a grid outage longer than 2h once power returns."""
    try:
        eid_consumption = role_get("realtime_consumption")
        if not eid_consumption:
            return
        e = index.get(eid_consumption)
        if not e:
            return
        state = e.get("state", "")
        if state in ("unavailable", "unknown"):
            started_at = mem_get("grid_outage_started_at")
            if not started_at:
                mem_set("grid_outage_started_at", now.isoformat())
        else:
            started_at = mem_get("grid_outage_started_at")
            if started_at:
                mem_set("grid_outage_started_at", "")
                try:
                    dt_started_at = datetime.fromisoformat(started_at)
                    duration_h = (now - dt_started_at).total_seconds() / 3600
                    if duration_h >= 2:
                        _alert_if_new(
                            "freezer_outage",
                            f"⚡ GRID OUTAGE FOR {duration_h:.1f}H\n━━━━━━━━━━━━━━━━━━\n"
                            f"Started: {dt_started_at.strftime('%H:%M')} | Restored: {now.strftime('%H:%M')}\n\n"
                            f"⚠️ Check the freezer — cold chain potentially compromised.",
                            delay_h=24
                        )
                except Exception:
                    pass
    except Exception as e:
        log.debug(f"Freezer alert: {e}")


def _detect_vacation_mode(now):
    """Enable vacation mode after 48h without Telegram or appliance activity."""
    try:
        last_msg = mem_get("last_message_telegram")
        if not last_msg:
            return
        try:
            dt_msg = datetime.fromisoformat(last_msg)
        except Exception:
            return
        if (now - dt_msg).total_seconds() / 3600 < 48:
            return
        if mem_get("vacation_mode") == "active":
            return
        try:
            conn = sqlite3.connect(DB_PATH)
            last_cycle = conn.execute(
                "SELECT MAX(started_at) FROM appliance_cycles WHERE started_at > datetime('now', '-48 hours')"
            ).fetchone()
            conn.close()
            if last_cycle and last_cycle[0]:
                return
        except Exception:
            pass
        mem_set("vacation_mode", "active")
        log.info("🏖️ Vacation mode activated (48h without interaction)")
        telegram_send(
            "🏖️ VACATION MODE ACTIVE\n━━━━━━━━━━━━━━━━━━\n"
            "No interaction since 48h.\n"
            "Reduced monitoring is active; critical alerts still run.\n\n"
            "Send any message to disable it."
        )
    except Exception as e:
        log.debug(f"Vacation mode: {e}")


def _auto_update_github():
    """Check GitHub for updates every 24h."""
    try:
        repo = "MrMortalMonkey/home-assistant-companion"
        branch = "main"
        files = ["config.py", "shared.py", "skills.py", "assistant.py"]
        last = mem_get("auto_update_last")
        if last:
            try:
                dt = datetime.fromisoformat(last)
                if (datetime.now() - dt).total_seconds() < 86400:
                    return
            except Exception:
                pass
        url_api = f"https://api.github.com/repos/{repo}/commits/{branch}"
        r_sha = requests.get(url_api, timeout=15)
        if r_sha.status_code != 200:
            return
        remote_sha = r_sha.json().get("sha", "")[:7]
        sha_local = mem_get("auto_update_sha") or ""
        if remote_sha == sha_local:
            mem_set("auto_update_last", datetime.now().isoformat())
            return
        log.info(f"🔄 Update available: {sha_local or '?'} → {remote_sha}")
        files_dl = {}
        for fname in files:
            url_raw = f"https://raw.githubusercontent.com/{repo}/{branch}/{fname}"
            r_dl = requests.get(url_raw, timeout=30)
            if r_dl.status_code != 200:
                log.error(f"Auto-update: unable to download {fname}")
                return
            files_dl[fname] = r_dl.text
        import py_compile, tempfile
        for fname, content in files_dl.items():
            try:
                with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                py_compile.compile(tmp_path, doraise=True)
                os.remove(tmp_path)
            except py_compile.PyCompileError as e:
                log.error(f"Auto-update: invalid syntax {fname}: {e}")
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
                return
        backup_dir = os.path.join(BASE_DIR, "versions")
        os.makedirs(backup_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        import shutil
        for fname in files:
            src = os.path.join(BASE_DIR, fname)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(backup_dir, f"{fname}.bak_{ts}"))
        for fname, content in files_dl.items():
            with open(os.path.join(BASE_DIR, fname), "w") as f:
                f.write(content)
        mem_set("auto_update_sha", remote_sha)
        mem_set("auto_update_last", datetime.now().isoformat())
        log.info(f"✅ Update applied: {remote_sha}")
        telegram_send(
            f"🔄 AUTOMATIC UPDATE\n━━━━━━━━━━━━━━━━━━\n"
            f"Version: {remote_sha}\nFiles: {', '.join(files)}\n"
            f"Restarting in 5 seconds..."
        )
        import subprocess
        time.sleep(5)
        subprocess.Popen(["systemctl", "restart", "assistant"])
    except Exception as e:
        log.error(f"Auto-update: {e}")



# =============================================================================
# =============================================================================

def _backup_auto_db(now):
    """Back up memory.db and config.json nightly at 3 AM, retaining 30 days."""
    try:
        if now.hour != 3 or now.minute > 5:
            return
        last = mem_get("backup_db_last")
        if last:
            try:
                if (now - datetime.fromisoformat(last)).total_seconds() < 72000:  # 20h
                    return
            except Exception:
                pass

        import shutil
        backup_dir = os.path.join(BASE_DIR, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        ts = now.strftime("%Y%m%d_%H%M")

        # Backup DB
        src_db = os.path.join(BASE_DIR, "memory.db")
        if os.path.exists(src_db):
            shutil.copy2(src_db, os.path.join(backup_dir, f"memory_{ts}.db"))

        # Backup config
        src_cfg = os.path.join(BASE_DIR, "config.json")
        if os.path.exists(src_cfg):
            shutil.copy2(src_cfg, os.path.join(backup_dir, f"config_{ts}.json"))

        # Purge > 30 days
        import glob
        cutoff = (now - timedelta(days=30)).strftime("%Y%m%d")
        for f in glob.glob(os.path.join(backup_dir, "memory_*.db")) + glob.glob(os.path.join(backup_dir, "config_*.json")):
            fname = os.path.basename(f)
            date_part = fname.split("_")[1][:8] if "_" in fname else ""
            if date_part and date_part < cutoff:
                os.remove(f)

        mem_set("backup_db_last", now.isoformat())
        log.info(f"💾 Backup DB + config → backups/{ts}")
    except Exception as e:
        log.error(f"Backup DB: {e}")


def cmd_score():
    """Score energy DPE dynamic of the home."""
    try:
        conn = sqlite3.connect(DB_PATH)
        now = datetime.now()

        # 1. Coverage solar (0-25 pts)
        score_solar = 0
        try:
            rows = conn.execute(
                "SELECT AVG(coverage_pct) FROM appliance_cycles WHERE ended_at IS NOT NULL AND started_at > datetime('now', '-30 days')"
            ).fetchone()
            if rows and rows[0]:
                score_solar = min(25, int(rows[0] / 4))  # 100% → 25 pts
        except Exception:
            pass

        score_eco = 0
        try:
            eco = get_savings_month(now.year, now.month)
            total_eco = sum(e[2] for e in eco) if eco else 0
            score_eco = min(25, int(total_eco * 2.5))  # 10€ → 25 pts
        except Exception:
            pass

        score_standby = 15  # Score max by default — lost if active standby loads
        try:
            nb_standby = conn.execute(
                "SELECT COUNT(*) FROM memory_store WHERE key_name LIKE 'standby_alert_%' AND updated_at > datetime('now', '-7 days')"
            ).fetchone()[0]
            score_standby = max(0, 15 - nb_standby * 3)  # -3 per standby
        except Exception:
            pass

        score_zigbee = 15
        try:
            nb_weak = conn.execute(
                "SELECT COUNT(*) FROM entity_map WHERE category NOT IN ('ignore') AND entity_id LIKE '%lqi%'"
            ).fetchone()[0]
            score_zigbee = 15 if nb_weak == 0 else max(5, 15 - nb_weak)
        except Exception:
            pass

        # 5. Off-peak optimization (0-10 pts)
        score_hchp = 0
        try:
            cycles_hc = conn.execute(
                "SELECT COUNT(*) FROM appliance_cycles WHERE ended_at IS NOT NULL AND started_at > datetime('now', '-30 days') AND CAST(strftime('%H', started_at) AS INTEGER) BETWEEN 0 AND 6"
            ).fetchone()[0]
            cycles_total = conn.execute(
                "SELECT COUNT(*) FROM appliance_cycles WHERE ended_at IS NOT NULL AND started_at > datetime('now', '-30 days')"
            ).fetchone()[0]
            if cycles_total > 0:
                pct_hc = cycles_hc / cycles_total * 100
                score_hchp = min(10, int(pct_hc / 10))  # 100% off-peak -> 10 pts
        except Exception:
            pass

        score_baselines = 0
        try:
            baseline_count = conn.execute("SELECT COUNT(*) FROM baselines WHERE sample_count >= 10").fetchone()[0]
            score_baselines = min(10, int(baseline_count / 50))  # 500 baselines -> 10 pts
        except Exception:
            pass

        conn.close()

        total = score_solar + score_eco + score_standby + score_zigbee + score_hchp + score_baselines

        if total >= 80:
            grade, emoji = "A", "🟢"
        elif total >= 60:
            grade, emoji = "B", "🟡"
        elif total >= 40:
            grade, emoji = "C", "🟠"
        else:
            grade, emoji = "D", "🔴"

        return (
            f"🏠 HOME ENERGY SCORE\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"{emoji} **Grade: {grade} — {total}/100**\n\n"
            f"☀️ Solar coverage: {score_solar}/25\n"
            f"💰 Savings: {score_eco}/25\n"
            f"🔌 Standby: {score_standby}/15\n"
            f"📡 Zigbee Network: {score_zigbee}/15\n"
            f"⏰ Off-peak optimization: {score_hchp}/10\n"
            f"📊 Baselines: {score_baselines}/10\n\n"
            f"Score updates weekly."
        )
    except Exception as e:
        return f"❌ Error score: {e}"


def cmd_export_pdf():
    """Generate and send a report PDF monthly by email."""
    try:
        now = datetime.now()
        conn = sqlite3.connect(DB_PATH)

        month = now.strftime("%B %Y")

        # Savings
        eco = get_savings_month(now.year, now.month)
        total_eco = sum(e[2] for e in eco) if eco else 0

        # Cycles
        cycles = conn.execute(
            "SELECT entity_id, COUNT(*), SUM(consumption_kwh), SUM(cost_grid) "
            "FROM appliance_cycles WHERE ended_at IS NOT NULL "
            "AND strftime('%Y-%m', started_at) = ? GROUP BY entity_id",
            (now.strftime("%Y-%m"),)
        ).fetchall()

        # Tokens
        tokens = get_token_usage()

        conn.close()

        # Build the report text (send by email)
        report = f"📊 MONTHLY REPORT — {month}\n"
        report += "=" * 40 + "\n\n"

        report += f"💰 SAVINGS: {total_eco:.2f}\n"
        if eco:
            for e in eco:
                report += f"  • {e[1]}: {e[2]:.2f}\n"

        report += f"\n🔄 APPLIANCE CYCLES:\n"
        for c in cycles:
            app = appliance_get(c[0])
            name = app["name"] if app and app.get("name") else c[0].split(".")[-1]
            report += f"  • {name}: {c[1]} cycles, {c[2]:.1f} kWh, {c[3]:.2f}\n"

        report += f"\n🤖 API TOKENS: {tokens.get('total_tokens', 0):,} ({tokens.get('total_cost', 0):.2f})\n"

        # Score
        score_txt = cmd_score()
        report += f"\n{score_txt}\n"

        # Send by email
        subject = f"[AI Companion] Report monthly — {month}"
        send_email(subject, report)

        return f"📧 Report {month} sent by email.\n\n{report[:500]}..."

    except Exception as e:
        return f"❌ Error export: {e}"


def cmd_advice_contract():
    """Compare the contract current with the alternatives and advise."""
    try:
        current_rate, _ = skill_get("pricing")
        if not current_rate or "type" not in current_rate:
            return "⚠️ No rate configured yet. Say your electricity rate in chat, for example: my rate is 0.14 per kWh."

        current_type = current_rate.get("type", "")
        provider = current_rate.get("provider", "")

        conn = sqlite3.connect(DB_PATH)
        now = datetime.now()

        # Consumption of the 30 lasts days
        try:
            consumption_total = conn.execute(
                "SELECT SUM(consumption_kwh) FROM appliance_cycles WHERE ended_at IS NOT NULL AND started_at > datetime('now', '-30 days')"
            ).fetchone()[0] or 0
        except Exception:
            consumption_total = 0

        conn.close()

        if consumption_total < 10:
            return "⚠️ Not enough data (< 10 kWh measured this month). Try again in a few weeks."

        # Monthly estimate.
        elapsed_days = now.day
        estimated_monthly_consumption = consumption_total / max(1, elapsed_days) * 30

        current_price = rate_current_kwh_price()
        current_cost = estimated_monthly_consumption * current_price

        # Compare with a few standard offers
        alternatives = [
            ("EDF Zen", 0.2516),
            ("EDF Zen WE", 0.2068),  # Average peak/off-peak
            ("TotalEnergies Essentielle", 0.2219),
            ("Octopus Eco-Consumption", 0.1992),
        ]

        result = f"💡 RATE PLAN ADVICE\n━━━━━━━━━━━━━━━━━━\n\n"
        result += f"Current contract: {provider} ({current_type})\n"
        result += f"Estimated consumption: {estimated_monthly_consumption:.0f} kWh/month\n"
        result += f"Estimated cost: {current_cost:.0f}/month\n\n"
        result += f"Alternatives:\n"

        for name, price in alternatives:
            cost_alt = estimated_monthly_consumption * price
            diff = current_cost - cost_alt
            emoji = "✅" if diff > 5 else "➖"
            result += f"  {emoji} {name}: ~{cost_alt:.0f}/month ({'+' if diff < 0 else '-'}{abs(diff):.0f})\n"

        result += f"\n⚠️ Simplified estimates. Check provider websites for exact rates."
        return result

    except Exception as e:
        return f"❌ Error advice contract: {e}"



# =============================================================================
# =============================================================================

def _detect_internet_outage(now):
    """If HA unreachable > 5 min → log. > 30 min → alert SMS. CRASH-PROOF."""
    try:
        states = ha_get("states")
        if states:
            if mem_get("ha_unreachable_since"):
                mem_set("ha_unreachable_since", "")
            return

        # HA unreachable
        started_at = mem_get("ha_unreachable_since")
        if not started_at:
            mem_set("ha_unreachable_since", now.isoformat())
            log.warning("⚠️ HA unreachable — starting monitoring")
            return

        try:
            dt_started_at = datetime.fromisoformat(started_at)
            minutes = (now - dt_started_at).total_seconds() / 60
        except Exception:
            return

        if minutes > 30:
            _alert_if_new(
                "outage_internet",
                f"🌐 HOME ASSISTANT INACCESSIBLE\n━━━━━━━━━━━━━━━━━━\n"
                f"Depuis {int(minutes)} min ({dt_started_at.strftime('%H:%M')})\n"
                f"Check your internet connection or HA.",
                delay_h=2
            )
            if minutes > 60 and CFG.get("sms_method"):
                try:
                    _alert_if_new(
                        "outage_internet_sms",
                        f"ALERTE: HA unreachable since {int(minutes)}min",
                        delay_h=6
                    )
                except Exception:
                    pass
    except Exception as e:
        log.debug(f"Corpure internet: {e}")


def _heartbeat_init_table():
    """Create the sensor_heartbeat table if it does not exist. Idempotent."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sensor_heartbeat (
                entity_id TEXT PRIMARY KEY,
                median_sec INTEGER,
                p95_sec INTEGER,
                p99_sec INTEGER,
                samples_count INTEGER,
                last_recompute TEXT,
                learning_started TEXT,
                learning_complete INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        log.debug(f"heartbeat_init_table: {e}")


_HEARTBEAT_SENSORS_PILIERS = [
    "sensor.ecojoko_realtime_consumption",
    "sensor.ecojoko_consumption_grid",
    "sensor.ecojoko_surplus_de_production",
    "sensor.ecojoko_indoor_humidity",
    "sensor.ecu_current_power",
    "sensor.ecu_today_energy",
    "sensor.solarbank_e1600_power_solar",
    "sensor.solarbank_e1600_state_of_charge",
]
_HEARTBEAT_SENSORS_TARIF = []


def _heartbeat_learn(entity_id):
    """Learn a sensor baseline from the last 7 days of HA history.
    
    Returns: (median_sec, p95_sec, p99_sec, samples_count) or None if there is not enough data.
    """
    try:
        from datetime import datetime, timedelta, timezone
        cfg = load_config()
        ha_url = cfg.get("ha_url")
        ha_token = cfg.get("ha_token")
        if not ha_url or not ha_token:
            return None
        
        start = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        url = f"{ha_url}/api/history/period/{start}?filter_entity_id={entity_id}&minimal_response&no_attributes"
        
        r = requests.get(url, 
                         headers={"Authorization": f"Bearer {ha_token}"},
                         timeout=30, verify=False)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data or not data[0] or len(data[0]) < 10:
            return None
        history = data[0]
        
        tss = []
        for entry in history:
            ts = entry.get("last_changed", entry.get("last_updated", ""))
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                tss.append(dt)
            except Exception:
                continue
        
        if len(tss) < 10:
            return None
        
        gaps = sorted([(tss[i+1] - tss[i]).total_seconds() for i in range(len(tss)-1)])
        n = len(gaps)
        median = int(gaps[n // 2])
        p95 = int(gaps[int(n * 0.95)]) if n >= 20 else int(gaps[-1])
        p99 = int(gaps[int(n * 0.99)]) if n >= 100 else int(gaps[-1])
        
        return (median, p95, p99, len(history))
    except Exception as e:
        log.debug(f"heartbeat_learn {entity_id}: {e}")
        return None


def _heartbeat_observe(index, now):
    """Skill heartbeat_core : monitors the update freshness of the sensors energy.
    
    3 phases automatic :
      1. Learning (J0 a J7) : noiseless observation, no alerts
      2. Calibration (J7) : calculation of thresholds since HA history of the 7 lasts days
      3. Monitoring (J7+) : alerts if gap > P99 × 2 (warning) or × 5 (critical)
    
    Recompute hebdomadaire for s'adapter aux saisons.
    
    Scope : 8 sensors energy core (Ecojoko + APSystems + Anker)
    
    Frequence : check all the 5 minutes (keeps via memory_store 'heartbeat_check')
    Cooldown alerts : 6h (warning), 1h (critical)
    
    CRASH-PROOF.
    
    Added 2026-04-25 - validated learning skill with MrMortalMonkey.
    """
    from datetime import datetime, timedelta, timezone
    try:
        last = mem_get("heartbeat_check")
        if last:
            try:
                if (now - datetime.fromisoformat(last)).total_seconds() < 300:
                    return
            except Exception:
                pass
        
        # Make sure the table exists.
        _heartbeat_init_table()
        
        conn = sqlite3.connect(DB_PATH)
        
        for entity_id in _HEARTBEAT_SENSORS_PILIERS:
            e = index.get(entity_id)
            
            # Read the existing baseline.
            row = conn.execute(
                "SELECT median_sec, p95_sec, p99_sec, samples_count, last_recompute, "
                "learning_started, learning_complete FROM sensor_heartbeat WHERE entity_id=?",
                (entity_id,)
            ).fetchone()
            
            if not row:
                conn.execute(
                    "INSERT INTO sensor_heartbeat (entity_id, learning_started, learning_complete) "
                    "VALUES (?, ?, 0)",
                    (entity_id, now.isoformat())
                )
                conn.commit()
                continue
            
            median_sec, p95_sec, p99_sec, samples, last_recompute, learning_started, learning_complete = row
            
            # Phase 1 : learning in progress ?
            if not learning_complete:
                try:
                    started = datetime.fromisoformat(learning_started)
                    days_learning = (now - started).total_seconds() / 86400
                except Exception:
                    days_learning = 0
                
                if days_learning < 7:
                    # Still learning, continue silently
                    continue
                
                result = _heartbeat_learn(entity_id)
                if not result:
                    continue
                median_sec, p95_sec, p99_sec, samples = result
                conn.execute(
                    "UPDATE sensor_heartbeat SET median_sec=?, p95_sec=?, p99_sec=?, "
                    "samples_count=?, last_recompute=?, learning_complete=1 WHERE entity_id=?",
                    (median_sec, p95_sec, p99_sec, samples, now.isoformat(), entity_id)
                )
                conn.commit()
                log.info(f"💓 heartbeat: {entity_id} learned (median {median_sec}s, P99 {p99_sec}s, {samples} samples)")
                continue
            
            # Phase 3 : monitoring active
            # Recompute hebdomadaire ?
            try:
                last_rc = datetime.fromisoformat(last_recompute)
                if (now - last_rc).total_seconds() > 7 * 86400:
                    result = _heartbeat_learn(entity_id)
                    if result:
                        median_sec, p95_sec, p99_sec, samples = result
                        conn.execute(
                            "UPDATE sensor_heartbeat SET median_sec=?, p95_sec=?, p99_sec=?, "
                            "samples_count=?, last_recompute=? WHERE entity_id=?",
                            (median_sec, p95_sec, p99_sec, samples, now.isoformat(), entity_id)
                        )
                        conn.commit()
                        log.info(f"💓 heartbeat: {entity_id} recalibrated (median {median_sec}s, P99 {p99_sec}s)")
            except Exception:
                pass
            
            # Calculer the gap current
            if not e:
                # Sensor missing from HA → critical alert
                _alert_if_new(
                    f"heartbeat_absent_{entity_id}",
                    f"🚨 HEARTBEAT — Sensor missing from HA\n━━━━━━━━━━━━━━━━━━\n"
                    f"  • {entity_id}\n"
                    f"The entity is no longer present in Home Assistant.\n"
                    f"Check the relevant integration.",
                    delay_h=1
                )
                continue
            
            last_updated = e.get("last_updated", "")
            if not last_updated:
                continue
            try:
                dt_upd = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
                if dt_upd.tzinfo is None:
                    dt_upd = dt_upd.replace(tzinfo=timezone.utc)
                now_utc = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
                gap_sec = (now_utc - dt_upd).total_seconds()
            except Exception:
                continue
            
            warning_threshold = p99_sec * 2 if p99_sec else 600
            critical_threshold = p99_sec * 5 if p99_sec else 1800
            
            if gap_sec > critical_threshold:
                _alert_if_new(
                    f"heartbeat_critical_{entity_id}",
                    f"🚨 HEARTBEAT CRITIQUE\n━━━━━━━━━━━━━━━━━━\n"
                    f"  • {entity_id}\n"
                    f"  • Gap: {int(gap_sec/60)}min (normal: median {int(median_sec/60) if median_sec else 0}min, P99 {int(p99_sec/60) if p99_sec else 0}min)\n"
                    f"  • State: {e.get('state', '?')}\n\n"
                    f"Probable failure: HA integration down, device unplugged, or manubillr clord offline.",
                    delay_h=1
                )
            elif gap_sec > warning_threshold:
                _alert_if_new(
                    f"heartbeat_warning_{entity_id}",
                    f"⚠️ HEARTBEAT — Sensor fige\n━━━━━━━━━━━━━━━━━━\n"
                    f"  • {entity_id}\n"
                    f"  • Gap: {int(gap_sec/60)}min (normal: median {int(median_sec/60) if median_sec else 0}min, P99 {int(p99_sec/60) if p99_sec else 0}min)\n"
                    f"  • State: {e.get('state', '?')}\n\n"
                    f"Monitoring to confirm: may be normal depending on context.",
                    delay_h=6
                )
        
        for entity_id in _HEARTBEAT_SENSORS_TARIF:
            e = index.get(entity_id)
            if not e:
                continue
            last_updated = e.get("last_updated", "")
            if not last_updated:
                continue
            try:
                dt_upd = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
                if dt_upd.tzinfo is None:
                    dt_upd = dt_upd.replace(tzinfo=timezone.utc)
                now_utc = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
                gap_h = (now_utc - dt_upd).total_seconds() / 3600
                if gap_h > 26:
                    _alert_if_new(
                        f"heartbeat_rate_{entity_id}",
                        f"⚠️ RATE HEARTBEAT — daily rollover\n━━━━━━━━━━━━━━━━━━\n"
                        f"  • {entity_id}\n"
                        f"  • Not updated for {gap_h:.1f}h (expected: 1 update/day at midnight)\n\n"
                        f"Peak/off-peak calculations may be based on stale values.\n"
                        f"Action: restart the Ecojoko integration in HA.",
                        delay_h=12
                    )
            except Exception:
                pass
        
        conn.close()
        mem_set("heartbeat_check", now.isoformat())
    except Exception as e:
        log.debug(f"heartbeat_observe: {e}")


def cmd_heartbeat():
    """Command /heartbeat : displays the status of the skill heartbeat_core.
    
    Show for each core sensor :
    - Learning phase (J/7) or calibrated thresholds (mediane, P99)
    - Gap current since last mise a day
    - Visual indicator by state (🟢 fresh / 🟡 warning / 🔴 alert)
    
    To reset learning, see LESSONS.md (command SQL via SSH).
    """
    from datetime import datetime, timezone
    try:
        _heartbeat_init_table()
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT entity_id, median_sec, p95_sec, p99_sec, samples_count, "
            "learning_started, learning_complete FROM sensor_heartbeat "
            "ORDER BY entity_id"
        ).fetchall()
        conn.close()
        
        cfg = load_config()
        gaps = {}
        try:
            r = requests.get(f"{cfg['ha_url']}/api/states",
                             headers={"Authorization": f"Bearer {cfg['ha_token']}"},
                             timeout=10, verify=False)
            states_list = r.json()
            now_utc = datetime.now(timezone.utc)
            for s in states_list:
                upd = s.get('last_updated', '')
                if upd:
                    try:
                        dt = datetime.fromisoformat(upd.replace('Z', '+00:00'))
                        gaps[s['entity_id']] = int((now_utc - dt).total_seconds() / 60)
                    except Exception:
                        pass
        except Exception:
            pass
        
        if not rows:
            return ("💓 HEARTBEAT — No sensors in learning phase.\n"
                    "The skill will start at the next cycle of polling (5 min max).")
        
        msg_lines = ["💓 HEARTBEAT — Key energy sensors", "━" * 28, ""]
        for row in rows:
            eid, med, p95, p99, samples, started, complete = row
            short = eid.replace("sensor.", "")
            gap_min = gaps.get(eid)
            gap_str = f"{gap_min}min" if gap_min is not None else "?"
            
            if not complete:
                try:
                    days = (datetime.now() - datetime.fromisoformat(started)).total_seconds() / 86400
                    msg_lines.append(f"📚 {short}")
                    msg_lines.append(f"   learning D{days:.1f}/D7")
                except Exception:
                    msg_lines.append(f"📚 {short} (learning)")
            else:
                med_str = f"{med//60}min" if med and med >= 60 else f"{med}s" if med else "?"
                p99_str = f"{p99//60}min" if p99 and p99 >= 60 else f"{p99}s" if p99 else "?"
                warning_threshold_min = (p99 * 2 / 60) if p99 else 999
                attention_threshold_min = (p95 / 60) if p95 else 999
                if gap_min is not None and gap_min > warning_threshold_min:
                    icon = "🔴"
                elif gap_min is not None and gap_min > attention_threshold_min:
                    icon = "🟡"
                else:
                    icon = "🟢"
                msg_lines.append(f"{icon} {short}")
                msg_lines.append(f"   med {med_str} · P99 {p99_str} · gap {gap_str} · {samples} samples")
        
        # Peak/off-peak rate sensors.
        msg_lines.append("")
        msg_lines.append("📅 Peak/off-peak rate sensors (1 update/day, threshold 26h)")
        for eid in _HEARTBEAT_SENSORS_TARIF:
            short = eid.replace("sensor.", "")
            gap = gaps.get(eid)
            if gap is None:
                gap_str = "?"
                icon = "⚪"
            else:
                gap_str = f"{gap//60}h{gap%60:02d}min" if gap > 60 else f"{gap}min"
                icon = "🔴" if gap/60 > 26 else "🟢"
            msg_lines.append(f"{icon} {short} · gap {gap_str}")
        
        return "\n".join(msg_lines)
    except Exception as e:
        return f"❌ /heartbeat : error {e}"


def _alert_zigbee_device_mort(index, now):
    """Detects devices Zigbee unavailable > 24h. CRASH-PROOF."""
    try:
        if now.hour != 9 or now.minute > 5:
            return  # Once per day at 9
        last = mem_get("zigbee_mort_check")
        if last:
            try:
                if (now - datetime.fromisoformat(last)).total_seconds() < 72000:
                    return
            except Exception:
                pass

        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT entity_id, friendly_name FROM entity_map WHERE category NOT IN ('ignore')"
        ).fetchall()
        conn.close()

        morts = []
        for eid, fname in rows:
            e = index.get(eid)
            if e and e.get("state") == "unavailable":
                last_changed = e.get("last_changed", "")
                if last_changed:
                    try:
                        dt_changed = datetime.fromisoformat(last_changed.replace("Z", "+00:00")).replace(tzinfo=None)
                        hours = (now - dt_changed).total_seconds() / 3600
                        if hours > 24:
                            morts.append((fname or eid, int(hours)))
                    except Exception:
                        pass

        if morts:
            list = "\n".join(f"  • {name} ({h}h)" for name, h in morts[:10])
            _alert_if_new(
                "zigbee_mort",
                f"📡 DEVICES UNAVAILABLE > 24H\n━━━━━━━━━━━━━━━━━━\n{list}\n\n"
                f"Check: dead battery, out of Zigbee range, or faulty device.",
                delay_h=24
            )

        mem_set("zigbee_mort_check", now.isoformat())
    except Exception as e:
        log.debug(f"Zigbee dead: {e}")


def _notif_tempo_ejp(now):
    """If contract Tempo/EJP, notify standby status on red days. CRASH-PROOF."""
    try:
        if now.hour != 19 or now.minute > 5:
            return
        rate, _ = skill_get("pricing")
        if not rate or rate.get("type") not in ("tempo", "ejp"):
            return
        last = mem_get("tempo_check_last")
        if last:
            try:
                if (now - datetime.fromisoformat(last)).total_seconds() < 72000:
                    return
            except Exception:
                pass

        # Call the RTE Tempo API
        try:
            url = "https://www.api-color-tempo.fr/api/dayTempo/tomorrow"
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                color = data.get("codeDay", 0)
                # 1=blue, 2=white, 3=red
                if color == 3:
                    telegram_send(
                        "🔴 RED TEMPO DAY TOMORROW\n━━━━━━━━━━━━━━━━━━\n"
                        "Very high rate tomorrow.\n"
                        "💡 Shift all appliances to tonight or the day after tomorrow.\n"
                        "🔌 Reduce heating if possible."
                    )
                elif color == 2:
                    telegram_send(
                        "⚪ JOUR BLANC TEMPO DEMAIN\n"
                        "Intermediate rate — use off-peak hours."
                    )
        except Exception:
            pass

        mem_set("tempo_check_last", now.isoformat())
    except Exception as e:
        log.debug(f"Tempo: {e}")



def _detect_water_leak(index, now):
    """If HA water sensor detects a leak → immediate alert. CRASH-PROOF."""
    try:
        for eid, e in index.items():
            if "monthture" in eid or "water_leak" in eid or "fuite" in eid:
                if e.get("state") in ("on", "True", "wet", "detected"):
                    fname = e.get("attributes", {}).get("friendly_name", eid)
                    _alert_if_new(
                        f"fuite_{eid}",
                        f"💧 WATER LEAK DETECTED\n━━━━━━━━━━━━━━━━━━\n"
                        f"Sensor: {fname}\n"
                        f"State: {e.get('state')}\n\n"
                        f"⚠️ Check immediately!",
                        delay_h=1
                    )
    except Exception as e:
        log.debug(f"Fuite eau: {e}")


def cmd_rooms():
    """Consumption by room — HA areas + entity name detection."""
    try:
        index = ha_get("states")
        if not index:
            return "❌ HA unreachable."

        index_dict = {e["entity_id"]: e for e in index}
        rooms = {}

        KNOWN_ROOMS = [
            "kitchen", "living_room", "bedroom", "office", "laundry_room", "garage",
            "salle of bain", "sdb", "entree", "corloir", "jardin", "terrasse",
            "attic", "basement", "cellier", "wc", "toilette",
        ]

        conn = sqlite3.connect(DB_PATH)
        solar_ids = set()
        try:
            rows = conn.execute(
                "SELECT entity_id FROM entity_map WHERE category IN ('energy_production', 'energy_battery', 'energy_solar')"
            ).fetchall()
            solar_ids = {r[0] for r in rows}
        except Exception:
            pass
        conn.close()

        for eid, e in index_dict.items():
            # Only power sensors.
            if "_power" not in eid or e.get("state") in ("unavailable", "unknown", ""):
                continue
            # Exclude solar production.
            if eid in solar_ids:
                continue
            try:
                watts = float(e["state"])
            except (ValueError, TypeError):
                continue
            if watts <= 0:
                continue

            # 1. Area HA
            area_id = shared._entity_areas.get(eid)
            room = shared._areas_id_to_name.get(area_id, "") if area_id else ""

            if not room:
                fname = e.get("attributes", {}).get("friendly_name", eid).lower()
                eid_low = eid.lower()
                for p in KNOWN_ROOMS:
                    if p in eid_low or p in fname:
                        room = p.capitalize()
                        break
                if not room:
                    if "bedroom" in eid_low:
                        parts = eid_low.split("bedroom")
                        if len(parts) > 1:
                            suffix = parts[1].replace("_", " ").strip()
                            room = f"Bedroom {suffix}".strip().title()
                        else:
                            room = "Bedroom"

            if not room:
                room = "Other"

            if room not in rooms:
                rooms[room] = []
            rooms[room].append((eid, watts, e.get("attributes", {}).get("friendly_name", eid)))

        if not rooms:
            return "📊 No room power data available."

        rooms_total = {p: sum(w for _, w, _ in devs) for p, devs in rooms.items()}
        total = sum(rooms_total.values())
        rooms_sorted = sorted(rooms_total.items(), key=lambda x: x[1], reverse=True)

        result = "🏠 CONSUMPTION BY ROOM\n━━━━━━━━━━━━━━━━━━\n\n"
        for room, watts in rooms_sorted:
            pct = int(watts / total * 100) if total > 0 else 0
            barre = "█" * max(1, pct // 5) + "░" * max(0, 20 - pct // 5)
            devs = sorted(rooms[room], key=lambda x: x[1], reverse=True)
            detail = ", ".join(f"{fn.split(' ')[-1]} {w:.0f}W" for _, w, fn in devs[:3])
            result += f"**{room}** : {watts:.0f}W ({pct}%)\n{barre}\n{detail}\n\n"

        result += f"**TOTAL : {total:.0f}W**"
        return result

    except Exception as e:
        return f"❌ Room error: {e}"



# =============================================================================
# =============================================================================

def _rollback_si_errors_repetees(now):
    """If 3+ crashes occur in 1h → rollback to the last backup. CRASH-PROOF."""
    try:
        if now.minute != 0:
            return  # Check all hours only

        import glob
        crash_log = os.path.join(BASE_DIR, "crash.log")
        if not os.path.exists(crash_log):
            return

        with open(crash_log) as f:
            content = f.read()

        recent_crashes = 0
        for line in content.split("\n"):
            if "CRASH" in line:
                try:
                    # Extract timestamp.
                    ts_str = line.split("CRASH")[1][:25].strip()
                    dt = datetime.fromisoformat(ts_str.replace(" ", "T")[:19])
                    if (now - dt).total_seconds() < 3600:
                        recent_crashes += 1
                except Exception:
                    pass

        if recent_crashes >= 3:
            backup_dir = os.path.join(BASE_DIR, "versions")
            backups = sorted(glob.glob(os.path.join(backup_dir, "skills.py.bak_*")), reverse=True)
            if backups:
                import shutil
                log.warning(f"⚠️ {recent_crashes} crashes in 1h — rollback to {os.path.basename(backups[0])}")
                shutil.copy2(backups[0], os.path.join(BASE_DIR, "skills.py"))
                telegram_send(
                    f"⚠️ ROLLBACK AUTOMATIQUE\n━━━━━━━━━━━━━━━━━━\n"
                    f"{recent_crashes} crashes detected in 1h.\n"
                    f"Rolling back to previors version.\n"
                    f"Restarting..."
                )
                import subprocess
                time.sleep(3)
                subprocess.Popen(["systemctl", "restart", "assistant"])
    except Exception as e:
        log.debug(f"Rollback auto: {e}")


def _monitoring_deploy_server(now):
    """Checks that the deploy server repond. CRASH-PROOF."""
    try:
        if now.minute not in (0, 30):
            return  # 2x per hour

        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            result = s.connect_ex(("127.0.0.1", 8501))
            s.close()
            if result == 0:
                return  # Port open = deploy server running
        except Exception:
            pass

        _alert_if_new(
            "deploy_server_down",
            "⚠️ Deploy server not responding on port 8501.\n"
            "Remote deployment is unavailable.",
            delay_h=6
        )
    except Exception as e:
        log.debug(f"Monitor deploy: {e}")


def cmd_known_appliances():
    """Show the known appliance library."""
    try:
        path = os.path.join(BASE_DIR, "KNOWN_APPLIANCES.json")
        if not os.path.exists(path):
            return "⚠️ File KNOWN_APPLIANCES.json not found."
        with open(path) as f:
            data = json.load(f)
        result = "📚 KNOWN APPLIANCES\n━━━━━━━━━━━━━━━━━━\n\n"
        for key, info in data.items():
            emoji = info.get("emoji", "")
            name = info.get("name", key)
            consumption = info.get("consumption_kwh_typique", [0, 0])
            duration = [info.get("duration_min_min", 0), info.get("duration_max_min", 0)]
            result += f"{emoji} **{name}**\n"
            result += f"  Power: {info.get('power_min_w', 0)}-{info.get('power_max_w', 0)}W\n"
            if duration[1] > 0:
                result += f"  Duration: {duration[0]}-{duration[1]} min\n"
            result += f"  Consumption: {consumption[0]}-{consumption[1]} kWh\n"
            if info.get("pieges"):
                result += f"  ⚠️ {info['pieges'][0]}\n"
            result += "\n"
        return result
    except Exception as e:
        return f"❌ Error: {e}"



# =============================================================================
# =============================================================================

_voice_scripts_last = {}

def _check_voice_scripts(index, now):
    """Detects execution of HA assistant_* scripts → processes + TTS Nest Hub. CRASH-PROOF."""
    global _voice_scripts_last
    try:
        scripts_map = {
            "script.assistant_energy": "energy",
            "script.assistant_score": "score",
            "script.assistant_debug": "debug",
            "script.assistant_rooms": "rooms",
            "script.assistant_contract": "contract",
            "script.assistant_machines": "appliances",
            "script.assistant_roi": "roi",
            "script.assistant_alerts": "watches",
            "script.assistant_solar": "solar",
            "script.assistant_bill": "bill",
        }

        found = 0
        for eid in scripts_map:
            if eid in index:
                found += 1
        if found == 0:
            return  # No scripts HA found

        for eid, cmd in scripts_map.items():
            e = index.get(eid)
            if not e:
                continue
            last_triggered = e.get("attributes", {}).get("last_triggered", "")
            if last_triggered == "None":
                last_triggered = ""
            prev = _voice_scripts_last.get(eid, "")

            if last_triggered and last_triggered != prev:
                _voice_scripts_last[eid] = last_triggered
                if not prev:
                    continue

                log.info(f"🎙️ Google Home: {cmd} (trigger: {last_triggered[:19]})")

                try:
                    response = handle_message(cmd)
                    tts = response.replace("**", "").replace("━", "").replace("═", "")
                    tts = tts.replace("\n\n", ". ").replace("\n", ". ")
                    tts = tts.replace("  ", " ").strip()
                    if len(tts) > 400:
                        tts = tts[:397] + "..."

                    media_players = CFG.get("tts_media_players", ["media_player.bedroom_main"])
                    for mp in media_players:
                        try:
                            ha_post("services/tts/google_translate_say", {
                                "entity_id": mp,
                                "message": tts
                            })
                        except Exception:
                            pass

                    log.info(f"🎙️ TTS sent: {tts[:80]}...")
                except Exception as e:
                    log.error(f"Voice script error: {e}")

    except Exception as e:
        log.debug(f"Check voice scripts: {e}")

def handle_message(text):
    t = text.strip().lower()
    if t.startswith("/"):
        t = t[1:]
    log.info(f"Message: {text[:80]}")

    if mem_get("ha_action_pending"):
        if t in ("yes", "y", "confirm", "confirmed", "ok", "okay", "do it", "go ahead"):
            pending = mem_get("ha_action_pending")
            mem_set("ha_action_pending", "")
            try:
                action = json.loads(pending)
                return ha_execute_service_action(
                    action.get("domain", ""),
                    action.get("service", ""),
                    action.get("entity_id", ""),
                    action.get("data", {}),
                )
            except Exception:
                return "No pending action to confirm."
        if t in ("no", "n", "cancel", "stop"):
            mem_set("ha_action_pending", "")
            return "Action cancelled."

    commands = {
        "audit": cmd_audit,
        "energy": cmd_energy, "energy": cmd_energy, "heating": cmd_energy,
        "solar": cmd_solar,
        "batteries": cmd_batteries,  
        "zigbee": cmd_zigbee,
        "nas": cmd_nas,
        "automations": cmd_automations, "automations": cmd_automations,
        "addons": cmd_addons,
        "budget": cmd_budget,
        "heartbeat": cmd_heartbeat,
        "debug": cmd_debug,
        "logs": cmd_logs,
        "memory_store": cmd_memory_store, "memory_store": cmd_memory_store,
        "scan": cmd_scan,
        "cycles": cmd_cycles,
        "summary": automatic_summary,
        "documentation": cmd_documentation,
        "report": cmd_audit,
        "export": cmd_script_export,    # ✅ Export SCRIPT
        "script": cmd_script_export,    # ✅ Export SCRIPT
        "ai": cmd_script_export,    # ✅ Export SCRIPT
        "diag_energy": cmd_diag_energy,  # 🔧 Diagnostic energy
        "diag_offpeak": cmd_diag_hc,            # 🔧 Search off-peak hours
        "diag_plugs": cmd_diag_plugs,    # 🔧 Diagnostic plugs
        "diag_ecojoko": cmd_diag_ecojoko,  # 🔧 Diagnostic Ecojoko
        "diag_nas": cmd_diag_nas,          # 🔧 Diagnostic NAS
        "diag_carto": cmd_diag_carto,      # 🔧 Diagnostic entity_map
        "clean_map": cmd_clean_carto,    # 🧹 Cleanup entity_map
        "baselines": cmd_baselines,        # 📊 Behavior baselines
        "skills": cmd_skills,              # 🧠 Skills autonomous
        "analysis": cmd_analysis,            # 🧠 Trigger analysis AI
        "expertise": cmd_expertise,        # 📚 Expertise accumulated
        "learning": cmd_learning, # 📕 Learning jorrnal
        "intelligence": cmd_intelligence,  # 🧠 Score + dashboard
        "roles": cmd_roles,                # 🎯 Roles auto-discovered
        "sms": cmd_sms,                    # 📱 Resend code SMS
        "md": cmd_md,                      # 📧 Send MD by email
        "rate": cmd_rate,                # ⚡ Rate electricity
                "roi": cmd_roi,                    # 📈 ROI tokens vs savings
                        "diag_weather": cmd_diag_weather,      # 🌦️ Diagnostic weather
        "diag_forecast": cmd_diag_forecast, # 🔧 Debug forecast
        "help": cmd_documentation,          # 📖 Help (alias)
        "commands": cmd_commands, "command": cmd_commands, "menu": cmd_commands,
        "watches": cmd_watches,
        "score": cmd_score,
        "dpe": cmd_score,
        "export": cmd_export_pdf,
        "pdf": cmd_export_pdf,
        "contract": cmd_advice_contract,
        "advice": cmd_advice_contract,
        "rooms": cmd_rooms,
        "rooms": cmd_rooms,
        "rooms": cmd_rooms,
        "known_appliances": cmd_known_appliances,
        "alerts": cmd_watches,  # 🔔 Dynamic alerts  # 📋 Menu buttons
        "programs": cmd_programs,      # 🔄 Learned appliance programs
        "appliances": cmd_appliances,          # 🔌 Appliances on plugs
        "monitoring": cmd_monitoring,    # 🛡️ Everything monitored
        "profile": cmd_profile,                # 👥 Household profile
        "savings": cmd_savings,          # 💰 Detail of the savings
        "dashboard": cmd_dashboard,          # 📊 Push stats to HA (Lovelace)
        "calendar": cmd_calendar,        # 📅 Events calendar HA
        "test_weather": cmd_test_weather,      # 🧪 Test monitoring weather
    }

    if t in commands:
        return commands[t]()


    if mem_get("pending_rate_step"):
        mem_set("pending_rate_step", "")

    pending = mem_get("pending_hour_machine")
    if pending == "yes":
        import re as _re_msg
        # Cancellation
        if t in ("no", "❌", "cancel", "not today"):
            mem_set("pending_hour_machine", "")
            return "✅ No appliance today."

        # Parse the hour : 12h30, 12h, 14:30, 13:00, 12, etc.
        match = _re_msg.match(r"^(\d{1,2})[h:]?(\d{0,2})$", t.replace(" ", ""))
        if match:
            target_hour = int(match.group(1))
            target_minutes = int(match.group(2)) if match.group(2) else 0
            if 0 <= target_hour <= 23 and 0 <= target_minutes <= 59:
                now_msg = datetime.now()
                target_time = now_msg.replace(hour=target_hour, minute=target_minutes, second=0)
                if target_time <= now_msg:
                    mem_set("pending_hour_machine", "")
                    return f"⚠️ {target_hour}h{target_minutes:02d} has already passed."

                mem_set("pending_hour_machine", "")
                mem_set("reminder_machine", target_time.isoformat())
                mem_set("reminder_machine_hour", f"{target_hour}h{target_minutes:02d}")
                telegram_send(
                    f"⏰ Appliance reminder set for {target_hour}h{target_minutes:02d}\n"
                    f"I'll monitor solar production and the washing machine plug."
                )
                log.info(f"⏰ Reminder machine : {target_hour}h{target_minutes:02d}")
                return ""
        # If not recognized, continue to normal commands
        mem_set("pending_hour_machine", "")

    if mem_get("pending_name_appliance"):
        mem_set("pending_name_appliance", "")
        mem_set("appliances_queue", "")

    # Reset a single appliance (change of machine)
    if t.startswith("programs reset "):
        name_machine = t.split(" ", 2)[2].strip() if len(t.split(" ")) > 2 else ""
        programs, _ = skill_get("machine_programs")
        if programs:
            # Search appliance by name
            found = False
            for eid, progs in list(programs.items()):
                app = appliance_get(eid)
                app_name = (app["name"] if app else "").lower()
                if name_machine.lower() in app_name or name_machine.lower() in eid.lower():
                    del programs[eid]
                    skill_set("machine_programs", programs)
                    found = True
                    return f"🔄 Programs for {app['name'] if app else eid} reset.\nNew learning starts at next cycle."
            if not found:
                return f"❌ Machine '{name_machine}' not found.\nType /appliances to see the list."
        return "❌ No programs recorded."

    if t.startswith("energy ") or t.startswith("energy "):
        arg = t.split(" ", 1)[1].strip()
        if arg in ("detail", "detail", "complete", "all"):
            return cmd_energy(detail=True)

    if any(phrase in t for phrase in (
        "daily energy usage",
        "current daily energy",
        "energy usage today",
        "today's energy",
        "todays energy",
        "kwh today",
    )):
        return cmd_energy()

    native_response = _ha_native_response(text)
    if native_response is not None:
        return native_response

    # Command /problem → auto-correction (with or without description)
    if t in ("problem", "problem"):
        return (
            "🤔 HELP ME UNDERSTAND\n"
            "You typed /problem but without details.\n\n"
            "📝 HOW TO REPORT A PROBLEM\n"
            "Be concise and specific:\n"
            "- What? (heat pump, Zigbee, energy, NAS, appliances…)\n"
            "- When? (now, yesterday, this morning…)\n"
            "- How? (won't start, offline, consuming too much…)\n\n"
            "📌 EXAMPLES\n"
            "✅ /problem Heat pump not heating despite 5°C outside\n"
            "✅ /problem 3 Zigbee devices are offline\n"
            "✅ /problem Grid consumption doubled suddenly"
        )
    if t.startswith("problem ") or t.startswith("problem "):
        description = t.split(" ", 1)[1].strip()
        return cmd_problem(description)

    # Free-form question: remember setup details, then answer with HA context.
    _capture_conversational_setup(text)
    states = ha_get("states")
    context = ha_get_context_intelligent(text, states)


    cal_count = context.count("📅 CALENDAR ")
    log.debug(f"Context to model: {len(context)} chars, calendar lines={cal_count}")
    result = call_llm(text, context)
    if result is None:
        return "I could not produce a useful response. Try the exact device, room, or sensor name."
    return result


def automatic_summary():
    """Generate and send a summary complete"""
    # Tracker interaction for vacation mode
    try:
        mem_set("last_message_telegram", datetime.now().isoformat())
        if mem_get("vacation_mode") == "active":
            mem_set("vacation_mode", "")
            telegram_send("🏠 Vacation mode disabled — welcome back!")
    except Exception:
        pass

    # Pending automation modification
    try:
        if mem_get("ha_automation_modify") == "yes":
            pending = mem_get("ha_automation_pending")
            if pending:
                mem_set("ha_automation_modify", "")
                context = ha_get_context_intelligent()
                msg_modif = (
                    f"Here is the automation currently being modified :\n{pending}\n\n"
                    f"Requested change: {text}\n\n"
                    f"Apply the change and return the corrected automation using ha_create_automation."
                )
                return call_llm(msg_modif, context)
    except Exception:
        pass
    telegram_send("📊 Automatic report generation in progress...")
    states = ha_get("states")
    if not states:
        telegram_send("❌ SUMMARY — HA unreachable")
        return

    conn = sqlite3.connect(DB_PATH)
    nb_cycles = conn.execute("SELECT COUNT(*) FROM appliance_cycles WHERE ended_at IS NOT NULL").fetchone()[0]
    total_consumption = conn.execute("SELECT SUM(consumption_kwh) FROM appliance_cycles WHERE ended_at IS NOT NULL").fetchone()[0] or 0
    cost_total = conn.execute("SELECT SUM(cost_eur) FROM appliance_cycles WHERE ended_at IS NOT NULL").fetchone()[0] or 0
    outage_count = conn.execute("SELECT COUNT(*) FROM zigbee_outages").fetchone()[0]
    abnormal_count = conn.execute("SELECT COUNT(*) FROM zigbee_outages WHERE status='abnormal'").fetchone()[0]
    conn.close()

    context = f"""Summary for {CFG.get('summary_days', 4)} days:

CYCLES: {nb_cycles}
Consumption: {total_consumption:.2f} kWh
Estimated cost: {cost_total:.2f}€

ZIGBEE NETWORK:
Detected outages: {outage_count}
Confirmed anomalies: {abnormal_count}

Mapping: {mem_get('discovery_count', 0)} entities learned
"""

    prompt = (
        "Generate a concise summary of autonomous home monitoring. "
        "Summarize: detected appliances, anomalies, alerts. "
        "End with 3 recommendations. Be concise."
    )
    summary = call_llm(prompt, context)
    nb_j = CFG.get('summary_days', 4)
    telegram_send(f"📊 SUMMARY {nb_j} DAYS\n━━━━━━━━━━━━━━━━━━\n{summary}")
    send_email(
        f"[AI Companion] Summary {nb_j} days — {datetime.now().strftime('%d/%m/%Y')}",
        summary
    )
    mem_set("last_summary", datetime.now().isoformat())
