# =============================================================================
# =============================================================================

import json
import logging
import os
import re
import random
import requests
import sqlite3
import smtplib
import time
import threading
import hashlib
import hmac
import urllib.request
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from logging.handlers import RotatingFileHandler
from config import *
import llm_provider

# =============================================================================
# =============================================================================
import os as _tz_os
_tz_os.environ['TZ'] = 'Europe/Paris'
import time as _tz_time
_tz_time.tzset()

__all__ = [
    "ANTI_DUPLICATE_SEC",
    "AUTO_GUERISON_COOLDOWN",
    "BASELINE_ENTITIES",
    "BASE_DIR",
    "CFG",
    "BEHAVIOR_PROMPT",
    "CONFIG_PATH",
    "DB_PATH",
    "MIN_WASHER_DURATION",
    "MIN_DISHWASHER_DURATION",
    "MIN_DRYER_DURATION",
    "GRACE_AFTER_SPIN",
    "GRACE_AFTER_WASH",
    "GRACE_AFTER_DRYING",
    "GRACE_AFTER_DISHWASHER",
    "HA_DOMAINES_AUTORISES",
    "HA_TOOLS",
    "WEEKLY_SUMMARY_HOUR",
    "EVENING_SUMMARY_HOUR",
    "WORKDAY_BRIEFING_HOUR",
    "WEEKEND_BRIEFING_HOUR",
    "MACHINE_DAYS",
    "LOG_PATH",
    "LOW_LQI",
    "MAX_DAILY_MESSAGES",
    "MODE",
    "PLUG_POLL_ACTIVE",
    "PLUG_POLL_IDLE",
    "ROLES_DEFINIS",
    "AUTO_HEAL_THRESHOLD",
    "CYCLE_START_W",
    "CYCLE_END_W",
    "APPLIANCE_TYPES",
    "VERSION",
    "_ErrorCaptureHandler",
    "_alert_if_new",
    "_areas_id_to_name",
    "_power_outage_alertd",
    "_anti_crease_detected",
    "_last_high_phase",
    "_eco_proactive_state",
    "_entities_already_detected",
    "_entity_areas",
    "_errors_buffer",
    "_errors_vues",
    "_est_hour_creuse_ranges",
    "_est_chosen_day",
    "_est_weekend_ou_ferie",
    "_state_plugs",
    "_grace_ended_at",
    "_injecter_lessons_founding",
    "_install_matplotlib_bg",
    "_intelligence_compteur",
    "_is_authorized_chat",
    "_md_last_hash",
    "_plugs_snapshot",
    "_powers_history",
    "_laundry_reminder_sent",
    "_snapshot_valide",
    "_watchdog",
    "_wizard_save_config",
    "transcrire_vocal",
    "_wizard_step",
    "add_history",
    "appliance_get",
    "appliance_set",
    "call_claude",
    "battery_get_last_alert",
    "battery_set",
    "battery_set_alert",
    "channel_locked",
    "entity_map_get",
    "entity_map_get_by_category",
    "entity_map_get_all",
    "entity_map_get_all_categories",
    "load_behavior_prompt",
    "code_auth",
    "last_audit",
    "pending_response",
    "enregistrer_saving",
    "known_entities_get_all",
    "known_entities_maj",
    "send_code_sms",
    "send_email",
    "filter_analyze_messages",
    "filter_apprendre_pattern",
    "generer_code_auth",
    "get_savings_month",
    "get_history",
    "get_token_usage",
    "ha_est_day",
    "ha_get",
    "ha_get_state",
    "ha_get_forecast",
    "ha_get_current_solar_production",
    "ha_post",
    "init_db",
    "load_config",
    "log",
    "log_token_usage",
    "mem_get",
    "mem_set",
    "role_decouvrir",
    "role_decouvrir_baselines",
    "role_get",
    "role_get_all",
    "role_set",
    "role_val",
    "skill_get",
    "skill_set",
    "rate_est_hour_creuse",
    "rate_get",
    "rate_current_kwh_price",
    "telegram_answer_callback",
    "telegram_get_updates",
    "telegram_send",
    "telegram_send_buttons",
    "telegram_send_photo",
    "check_budget",
    "check_code",
    "zigbee_absence_create",
    "zigbee_absence_get",
    "zigbee_absence_retour",
    "zigbee_absence_status",
]

# =============================================================================
# LOGGING
# =============================================================================
_log_level = logging.DEBUG if MODE == "DEV" else logging.WARNING
_log_format = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
_file_handler = RotatingFileHandler(LOG_PATH, maxBytes=5*1024*1024, backupCount=3)
_file_handler.setFormatter(_log_format)
_file_handler.setLevel(_log_level)
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_log_format)
_console_handler.setLevel(_log_level)
logging.basicConfig(level=_log_level, handlers=[_file_handler, _console_handler])
log = logging.getLogger(__name__)





# =============================================================================
# GLOBAL STATE VARIABLES
# =============================================================================

BASELINE_ENTITIES = {
    "sensor.ecojoko_realtime_consumption": "consumption_edf_w",
    "sensor.ecu_current_power": "production_aps_w",
    "sensor.air_water_heat_pump_energy_current": "consumption_heat_pump_w",
    "sensor.ecojoko_temperature_interieure": "temp_interieure",
    "sensor.ecojoko_temperature_exterieure": "temp_exterieure",
}
HA_DOMAINES_AUTORISES = {"light", "switch", "lock", "cover", "climate", "fan", "vacuum", "media_player", "scene", "script"}
HA_TOOLS = [
    {
        "name": "ha_call_service",
        "description": "Calls a Home Assistant service to control a device. "
                       "Use DIRECTLY the entity_id visible in the HA state. "
                       "NEVER ask for textual confirmation — the system handles confirmation via buttons.",
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "HA domain: light, switch, lock, cover, climate, fan, vacuum, media_player, scene, script"
                },
                "service": {
                    "type": "string",
                    "description": "Service: turn_on, turn_off, toggle, lock, unlock, open_cover, close_cover, set_temperature, etc."
                },
                "entity_id": {
                    "type": "string",
                    "description": "Exact entity ID (e.g.: lock.front_door, light.living_room)"
                },
                "data": {
                    "type": "object",
                    "description": "Optional data (brightness, temperature, etc.)",
                    "default": {}
                }
            },
            "required": ["domain", "service", "entity_id"]
        }
    },
    {
        "name": "ha_create_automation",
        "description": "Creates an automation in Home Assistant. Searches for exact entity_ids in the provided HA state. NEVER asks the user to look up entities.",
        "input_schema": {
            "type": "object",
            "properties": {
                "alias": {"type": "string", "description": "Automation name"},
                "description": {"type": "string", "description": "Automation description"},
                "trigger": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "HA triggers (e.g.: [{platform: numeric_state, entity_id: sensor.xxx, above: 99}])"
                },
                "condition": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Optional conditions"
                },
                "action": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "HA actions (e.g.: [{service: switch.turn_on, target: {entity_id: switch.xxx}}])"
                },
                "mode": {"type": "string", "description": "single, restart, queued, parallel"}
            },
            "required": ["alias", "trigger", "action"]
        }
    },
    {
        "name": "ha_search_entities",
        "description": "Searches for entities in Home Assistant by keyword. "
                       "Use this tool BEFORE creating an automation to find the exact entity_ids. "
                       "Returns all matching entity_ids, states and attributes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Search keyword (e.g.: anker, battery, temperature, light, lock, cover)"
                },
                "domain": {
                    "type": "string",
                    "description": "Filter by HA domain (optional): sensor, switch, light, cover, climate, lock, number, select, binary_sensor, automation"
                }
            },
            "required": ["keyword"]
        }
    },
    {
        "name": "ha_create_watch",
        "description": "Creates an automatic alert on one or more HA devices. "
                       "The assistant will check every minute and send a Telegram notification "
                       "when the condition is met. "
                       "Examples: alert if an ingreenr goes offline, if a temperature exceeds a threshold, "
                       "if a door stays open, if a light is on at night, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_pattern": {
                    "type": "string",
                    "description": "Entity ID or glob pattern (e.g.: sensor.ecu_* for all ingreenrs)"
                },
                "condition": {
                    "type": "string",
                    "description": "unavailable, offline, equals, not_equals, above, below, changes"
                },
                "state_value": {
                    "type": "string",
                    "description": "Threshold value (e.g.: 95 for above 95, on for equals on). Empty for unavailable/offline/changes."
                },
                "message": {
                    "type": "string",
                    "description": "Alert message. Variables: {entity_id}, {state}, {friendly_name}"
                },
                "cooldown_min": {
                    "type": "number",
                    "description": "Minimum delay between identical alerts (in minutes). Default: 60.",
                    "default": 60
                }
            },
            "required": ["entity_pattern", "condition", "message"]
        }
    }
]
_areas_id_to_name = {}
_power_outage_alertd = False
_anti_crease_detected = {}     # {entity_id: datetime} — start of of-wrinkle cycle detected
_last_high_phase = {}    # {entity_id: "C"/"E"/"L"} — last phase > SEUIL_FIN seen
_eco_proactive_state = {}
_entities_already_detected = set()
_entity_areas = {}
_errors_buffer = []  # [(timestamp, message, source)]
_errors_vues = {}    # {signature: last_reported} anti-spam
_state_plugs           = {}
_grace_ended_at             = {}
_intelligence_compteur = 0  # Cycle counter for periodic actions
_md_last_hash = None
_plugs_snapshot = {}       # Continuous snapshot: {entity_id: "on"/"off"}
_powers_history = {}   # {entity_id: [(timestamp, watts), ...]}
_laundry_reminder_sent = {}     # {entity_id: True} — "warm laundry" reminder already sent
_snapshot_valide = False     # True after at least 2 normal cycles
_watchdog = {
    "monitoring_last_run" : datetime.now(),
    "plugs_last_run"     : datetime.now(),
    "polling_last_update" : datetime.now(),
    "errors"             : [],
    "offset_last"         : None,
    "offset_blocked_since": None,
}
channel_locked = True
code_auth = None
last_audit = 0
pending_response = {}

def _install_matplotlib_bg():
    try:
        import matplotlib
    except ImportError:
        import subprocess
        subprocess.run(["pip3", "install", "matplotlib", "--break-system-heat_pumpkages", "-q"], timeout=300)

threading.Thread(target=_install_matplotlib_bg, daemon=True).start()


class _ErrorCaptureHandler(logging.Handler):
    """Captures all log.error() calls into a buffer for periodic analysis."""
    def emit(self, record):
        if record.levelno >= logging.ERROR:
            try:
                msg = self.format(record)
                # Signature = message cleaned of variable numbers for grouping
                import re as _re
                sig = _re.sub(r'\d+', '#', record.getMessage())[:80]
                _errors_buffer.append((datetime.now().isoformat(), msg[:300], sig))
                if len(_errors_buffer) > 200:
                    _errors_buffer.pop(0)
            except Exception:
                pass


def load_config():
    """Loads config.json. If missing, launches the installation wizard."""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        log.info("✅ Config loaded")
        return cfg

    print("\n" + "=" * 50)
    print("🏠 Home Assistant AI Companion — First startup")
    print("=" * 50)
    print("\nThis wizard will configure your assistant.")
    print("You will need:")
    print("  1. A Telegram bot (created via @BotFather)")
    print("  2. Home Assistant accessible (URL + token)")
    print("  3. An AI API key (Anthropic, OpenAI, or other supported provider)\n")

    # Step 1: Telegram Token (only mandatory CLI question)
    telegram_token = input("🤖 Telegram bot token (from @BotFather): ").strip()
    if not telegram_token or ":" not in telegram_token:
        print("❌ Invalid token. Expected format: 1234567890:ABCDEF...")
        raise SystemExit(1)

    # Validate the token
    try:
        r = requests.get(f"https://api.telegram.org/bot{telegram_token}/getMe", timeout=10)
        if r.status_code != 200:
            print(f"❌ Invalid Telegram token (HTTP {r.status_code})")
            raise SystemExit(1)
        bot_name = r.json().get("result", {}).get("first_name", "Bot")
        print(f"✅ Bot connected: {bot_name}")
    except requests.RequestException as e:
        print(f"❌ Unable to contact Telegram: {e}")
        raise SystemExit(1)

    # Step 2: Detect chat_id
    print(f"\n📱 Send a message to your bot on Telegram.")
    print(f"   (Any message, just to detect your chat_id)")
    print(f"   Waiting...", end="", flush=True)

    chat_id = None
    for _ in range(120):  # 2 minutes max
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{telegram_token}/getUpdates",
                params={"timeout": 5, "allowed_updates": json.dumps(["message"])},
                timeout=10
            )
            if r.status_code == 200:
                updates = r.json().get("result", [])
                for u in reversed(updates):
                    if "message" in u and "chat" in u["message"]:
                        chat_id = str(u["message"]["chat"]["id"])
                        break
            if chat_id:
                break
            print(".", end="", flush=True)
        except Exception:
            time.sleep(2)

    if not chat_id:
        print("\n❌ Timeout — no message received. Restart the script and send a message to the bot.")
        raise SystemExit(1)

    print(f"\n✅ Chat ID detected: {chat_id}")

    # Create minimal config
    cfg = {
        "telegram_token": telegram_token,
        "telegram_chat_id": chat_id,
        "ha_url": "",
        "ha_token": "",
        "llm_provider": "anthropic",
        "anthropic_api_key": "",
        "openai_api_key": "",
        "openrouter_api_key": "",
        "ollama_host": "http://localhost:11434",
        "lmstudio_host": "http://localhost:1234",
        "poll_interval_sec": 2,
        "audit_interval_sec": 1800,
        "llm_monthly_budget_usd": 10,
        "anthropic_monthly_budget_usd": 10,
    }
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    log.info("✅ Minimal config created")

    # Send welcome message on Telegram
    msg = (
        "🏠 WELCOME — Home Assistant AI Companion\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "I am your AI home automation assistant.\n"
        "I will guide you through the configuration.\n\n"
        "📡 STEP 1/4 — Home Assistant\n"
        "Send me the URL of your Home Assistant.\n\n"
        "Examples:\n"
        "  • http://192.168.1.100:8123\n"
        "  • http://homeassistant.local:8123\n"
        "  • https://my-ha.duckdns.org"
    )
    requests.post(
        f"https://api.telegram.org/bot{telegram_token}/sendMessage",
        json={"chat_id": chat_id, "text": msg}
    )

    # Mark wizard as in progress
    cfg["_wizard_step"] = "ha_url"
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

    print("\n✅ Configuration continues on Telegram.")
    print("   Answer the bot's questions to complete the installation.\n")

    return cfg



CFG = load_config()

# sms_method migration
if "sms_method" not in CFG and not CFG.get("_wizard_step"):
    if CFG.get("free_mobile_user") and CFG.get("free_mobile_pass"):
        CFG["sms_method"] = "free_mobile"
    elif CFG.get("ha_notify_service"):
        CFG["sms_method"] = "ha_notify"
    elif CFG.get("smtp_host") and CFG.get("email_dest"):
        CFG["sms_method"] = "email"
    else:
        CFG["sms_method"] = "free_mobile"
    with open(CONFIG_PATH, "w") as f:
        json.dump(CFG, f, indent=2)
    log.info(f"Migration: sms_method={CFG['sms_method']}")

def _is_authorized_chat(chat_id):
    """Checks if a chat_id is authorized — supports multi-user.
    Config: telegram_chat_id can be a single ID or a comma-separated list.
    Ex: "123456789" or "123456789,987654321" """
    allowed = str(CFG.get("telegram_chat_id", ""))
    if "," in allowed:
        return str(chat_id) in [x.strip() for x in allowed.split(",")]
    return str(chat_id) == allowed


def init_db():
    conn = sqlite3.connect(DB_PATH)

    conn.execute('''CREATE TABLE IF NOT EXISTS memory_store (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key_name TEXT UNIQUE, value_text TEXT, updated_at TEXT
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        month TEXT UNIQUE, tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        role TEXT, content TEXT, created_at TEXT
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id TEXT UNIQUE, state TEXT, attributes TEXT, updated_at TEXT
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS entity_map (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id TEXT UNIQUE, category TEXT, subcategory TEXT,
        room TEXT, friendly_name TEXT, learned_at TEXT
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS batteries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id TEXT UNIQUE, room TEXT,
        last_value INTEGER, last_alert TEXT, updated_at TEXT
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS known_entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id TEXT UNIQUE,
        category TEXT,
        last_seen TEXT,
        missing_since TEXT
    )''')

    # Device cycles (washing machines, dryers, etc.)
    conn.execute('''CREATE TABLE IF NOT EXISTS watches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_pattern TEXT NOT NULL,
        condition TEXT NOT NULL,
        state_value TEXT DEFAULT '',
        message TEXT NOT NULL,
        cooldown_min INTEGER DEFAULT 60,
        last_triggered TEXT DEFAULT '',
        active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT ''
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS appliance_cycles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id TEXT,
        friendly_name TEXT,
        started_at TEXT,
        ended_at TEXT,
        duration_min INTEGER,
        consumption_kwh REAL,
        cost_eur REAL,
        solar_production_w INTEGER,
        created_at TEXT
    )''')

    # Migration: add profile + program columns if missing
    try:
        conn.execute("SELECT program FROM appliance_cycles LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE appliance_cycles ADD COLUMN program TEXT")
        conn.execute("ALTER TABLE appliance_cycles ADD COLUMN profile_json TEXT")
        log.info("📊 Migration: columns program + profile_json added to appliance_cycles")

    # Survives restarts → no longer need /api/history or CSV
    conn.execute('''CREATE TABLE IF NOT EXISTS cycle_measurements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id TEXT,
        watts REAL,
        ts TEXT
    )''')
    # Index for fast reading by entity_id
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cycle_measurements_eid ON cycle_measurements(entity_id)")

    # The user says "this plug has the washing machine"
    conn.execute('''CREATE TABLE IF NOT EXISTS appliances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id TEXT UNIQUE,
        appliance_type TEXT,
        custom_name TEXT,
        monitored INTEGER DEFAULT 1,
        created_at TEXT
    )''')

    # Every energy-saving action is tracked here.
    # This table justifies every token spent.
    conn.execute('''CREATE TABLE IF NOT EXISTS savings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT,
        description TEXT,
        euros REAL,
        kwh_saved REAL,
        source TEXT,
        created_at TEXT
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS zigbee_outages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id TEXT UNIQUE,
        offline_since TEXT,
        status TEXT,  -- 'normal', 'abnormal', 'pending'
        alert_sent TEXT,
        back_online TEXT
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS pending_entities (
        entity_id     TEXT PRIMARY KEY,
        friendly_name TEXT,
        proposed_category TEXT,
        description   TEXT,
        question_asked INTEGER DEFAULT 0,
        response       TEXT,
        created_at    TEXT
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS baselines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id TEXT,
        weekday INTEGER,
        hour INTEGER,
        avg_value REAL,
        sample_count INTEGER,
        updated_at TEXT,
        UNIQUE(entity_id, weekday, hour)
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS skills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        data TEXT,
        learning_count INTEGER DEFAULT 0,
        updated_at TEXT
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS expertise (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT,
        insight TEXT,
        confidence REAL DEFAULT 0.5,
        nb_validations INTEGER DEFAULT 0,
        source TEXT,
        created_at TEXT,
        updated_at TEXT
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS decisions_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT,
        context TEXT,
        result TEXT,
        success INTEGER DEFAULT -1,
        created_at TEXT
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS hypotheses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        statement TEXT,
        category TEXT,
        condition_test TEXT,
        predictions INTEGER DEFAULT 0,
        confirmations INTEGER DEFAULT 0,
        refutations INTEGER DEFAULT 0,
        confidence REAL DEFAULT 0.5,
        active INTEGER DEFAULT 1,
        created_at TEXT,
        updated_at TEXT
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS intelligence_score (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT UNIQUE,
        score_global REAL,
        expertise_count INTEGER,
        active_hypothesis_count INTEGER,
        prediction_rate REAL,
        skill_count INTEGER,
        baseline_count INTEGER,
        daily_failure_count INTEGER,
        daily_success_count INTEGER,
        estimated_savings REAL DEFAULT 0,
        details TEXT
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS message_filters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pattern TEXT UNIQUE,
        action TEXT DEFAULT 'block',
        reason TEXT,
        applied_count INTEGER DEFAULT 0,
        false_positive_count INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1,
        created_at TEXT,
        updated_at TEXT
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS message_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message TEXT,
        sent INTEGER DEFAULT 1,
        filter_reason TEXT,
        feedback TEXT,
        created_at TEXT
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS roles (
        role TEXT PRIMARY KEY,
        entity_id TEXT,
        confidence REAL DEFAULT 0.5,
        source TEXT,
        updated_at TEXT
    )''')

    def _hx(value):
        return bytes.fromhex(value).decode()

    def _table_exists(table_name):
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        ).fetchone() is not None

    def _columns(table_name):
        if not _table_exists(table_name):
            return set()
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}

    def _ensure_columns(table_name, column_defs):
        if not _table_exists(table_name):
            return
        cols = _columns(table_name)
        for column_name, column_type in column_defs:
            if column_name not in cols:
                try:
                    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                    cols.add(column_name)
                except Exception as ex:
                    log.warning(f"Column add skipped for {table_name}.{column_name}: {ex}")

    def _copy_table(old_hex, new_table, column_pairs):
        old_table = _hx(old_hex)
        if not _table_exists(old_table):
            return
        old_cols = _columns(old_table)
        new_cols = _columns(new_table)
        selected = [(old_col, new_col) for old_col, new_col in column_pairs
                    if old_col in old_cols and new_col in new_cols]
        if not selected:
            return
        target = ", ".join(new_col for _, new_col in selected)
        source = ", ".join(old_col for old_col, _ in selected)
        try:
            conn.execute(
                f"INSERT OR IGNORE INTO {new_table} ({target}) "
                f"SELECT {source} FROM {old_table}"
            )
        except Exception as ex:
            log.warning(f"Schema migration skipped for {new_table}: {ex}")

    def _copy_columns(table_name, column_pairs):
        if not _table_exists(table_name):
            return
        cols = _columns(table_name)
        for old_col, new_col in column_pairs:
            if old_col in cols and new_col in cols:
                try:
                    conn.execute(
                        f"UPDATE {table_name} SET {new_col}=COALESCE({new_col}, {old_col}) "
                        f"WHERE {new_col} IS NULL"
                    )
                except Exception as ex:
                    log.warning(f"Column migration skipped for {table_name}.{new_col}: {ex}")

    _ensure_columns("tokens", [("month", "TEXT")])
    _ensure_columns("baselines", [("weekday", "INTEGER"), ("hour", "INTEGER"), ("avg_value", "REAL"), ("sample_count", "INTEGER")])
    _ensure_columns("skills", [("name", "TEXT"), ("data", "TEXT"), ("learning_count", "INTEGER DEFAULT 0")])
    _ensure_columns("expertise", [("category", "TEXT"), ("confidence", "REAL DEFAULT 0.5")])
    _ensure_columns("decisions_log", [("context", "TEXT"), ("result", "TEXT"), ("success", "INTEGER DEFAULT -1")])
    _ensure_columns("hypotheses", [("statement", "TEXT"), ("category", "TEXT"), ("confidence", "REAL DEFAULT 0.5")])
    _ensure_columns("intelligence_score", [
        ("expertise_count", "INTEGER"), ("active_hypothesis_count", "INTEGER"),
        ("prediction_rate", "REAL"), ("skill_count", "INTEGER"),
        ("baseline_count", "INTEGER"), ("daily_failure_count", "INTEGER"),
        ("daily_success_count", "INTEGER"), ("estimated_savings", "REAL DEFAULT 0")
    ])

    _copy_table("6d656d6f697265", "memory_store", [
        ("id", "id"), (_hx("636c65"), "key_name"), (_hx("76616c657572"), "value_text"), ("updated_at", "updated_at")
    ])
    _copy_table("686973746f7269717565", "history", [
        ("id", "id"), ("role", "role"), (_hx("636f6e74656e75"), "content"), ("created_at", "created_at")
    ])
    _copy_table("656e7469746573", "entities", [
        ("id", "id"), ("entity_id", "entity_id"), ("state", "state"), ("attributes", "attributes"), ("updated_at", "updated_at")
    ])
    _copy_table("636172746f67726170686965", "entity_map", [
        ("id", "id"), ("entity_id", "entity_id"), (_hx("63617465676f726965"), "category"),
        (_hx("736f75735f63617465676f726965"), "subcategory"), (_hx("7069656365"), "room"),
        ("friendly_name", "friendly_name"), (_hx("6170707269735f6c65"), "learned_at")
    ])
    _copy_table("656e74697465735f636f6e6e756573", "known_entities", [
        ("id", "id"), ("entity_id", "entity_id"), (_hx("63617465676f726965"), "category"),
        (_hx("76755f6c615f6465726e696572655f666f6973"), "last_seen"),
        (_hx("646973706172755f646570756973"), "missing_since")
    ])
    _copy_table("6379636c65735f617070617265696c73", "appliance_cycles", [
        ("id", "id"), ("entity_id", "entity_id"), ("friendly_name", "friendly_name"),
        (_hx("6465627574"), "started_at"), (_hx("66696e"), "ended_at"),
        (_hx("64757265655f6d696e"), "duration_min"), (_hx("636f6e736f5f6b7768"), "consumption_kwh"),
        (_hx("636f75745f657572"), "cost_eur"), (_hx("70726f64756374696f6e5f736f6c616972655f77"), "solar_production_w"),
        ("created_at", "created_at"), ("program", "program"), ("profile_json", "profile_json")
    ])
    _copy_table("6379636c655f6d657375726573", "cycle_measurements", [
        ("id", "id"), ("entity_id", "entity_id"), ("watts", "watts"), ("ts", "ts")
    ])
    _copy_table("617070617265696c73", "appliances", [
        ("id", "id"), ("entity_id", "entity_id"), (_hx("747970655f617070617265696c"), "appliance_type"),
        (_hx("6e6f6d5f706572736f6e6e616c697365"), "custom_name"), (_hx("7375727665696c6c6572"), "monitored"),
        ("created_at", "created_at")
    ])
    _copy_table("65636f6e6f6d696573", "savings", [
        ("id", "id"), ("type", "type"), ("description", "description"), ("euros", "euros"),
        (_hx("6b77685f65636f6e6f6d69736573"), "kwh_saved"), ("source", "source"), ("created_at", "created_at")
    ])
    _copy_table("7a69676265655f616273656e636573", "zigbee_outages", [
        ("id", "id"), ("entity_id", "entity_id"), (_hx("686f72735f6c69676e655f646570756973"), "offline_since"),
        (_hx("737461747574"), "status"), (_hx("616c657274655f656e766f796565"), "alert_sent"),
        (_hx("7265746f75725f656e5f6c69676e65"), "back_online")
    ])
    _copy_table("656e74697465735f656e5f617474656e7465", "pending_entities", [
        ("entity_id", "entity_id"), ("friendly_name", "friendly_name"),
        (_hx("63617465676f7269655f70726f706f736565"), "proposed_category"),
        ("description", "description"), (_hx("7175657374696f6e5f706f736565"), "question_asked"),
        (_hx("7265706f6e7365"), "response"), ("created_at", "created_at")
    ])
    _copy_table("66696c7472655f6d65737361676573", "message_filters", [
        ("id", "id"), ("pattern", "pattern"), ("action", "action"), (_hx("726169736f6e"), "reason"),
        (_hx("6e625f6170706c69717565"), "applied_count"), (_hx("6e625f666175785f706f7369746966"), "false_positive_count"),
        (_hx("6163746966"), "active"), ("created_at", "created_at"), ("updated_at", "updated_at")
    ])
    _copy_table("6d657373616765735f6c6f67", "message_log", [
        ("id", "id"), ("message", "message"), (_hx("656e766f7965"), "sent"),
        (_hx("726169736f6e5f66696c747265"), "filter_reason"), ("feedback", "feedback"), ("created_at", "created_at")
    ])

    _copy_columns("tokens", [(_hx("6d6f6973"), "month")])
    _copy_columns("baselines", [
        (_hx("6a6f75725f73656d61696e65"), "weekday"), (_hx("6865757265"), "hour"),
        (_hx("76616c6575725f6d6f79656e6e65"), "avg_value"), (_hx("6e625f6d657375726573"), "sample_count")
    ])
    _copy_columns("skills", [(_hx("6e6f6d"), "name"), (_hx("646f6e6e656573"), "data"), (_hx("6e625f61707072656e7469737361676573"), "learning_count")])
    _copy_columns("expertise", [(_hx("63617465676f726965"), "category"), (_hx("636f6e6669616e6365"), "confidence")])
    _copy_columns("decisions_log", [(_hx("636f6e7465787465"), "context"), (_hx("726573756c746174"), "result"), (_hx("737563636573"), "success")])
    _copy_columns("hypotheses", [(_hx("656e6f6e6365"), "statement"), (_hx("63617465676f726965"), "category"), (_hx("636f6e6669616e6365"), "confidence")])

    conn.commit()
    conn.close()
    log.info("✅ Database initialized")

    # Guaranteed auto-unlock: last_unlock in SQLite (24h)

    # ═══ PURGE DUPLICATE EXPERTISE ═══
    try:
        conn_purge = sqlite3.connect(DB_PATH)
        count_before = conn_purge.execute("SELECT COUNT(*) FROM expertise").fetchone()[0]
        if count_before > 50:
            # Keep founding lessons + top 30 by confidence
            founding = [r[0] for r in conn_purge.execute(
                "SELECT id FROM expertise WHERE source LIKE 'founding_lesson%'"
            ).fetchall()]
            others_top = [r[0] for r in conn_purge.execute(
                "SELECT id FROM expertise WHERE source NOT LIKE 'founding_lesson%' ORDER BY confidence DESC LIMIT 30"
            ).fetchall()]
            keep_ids = set(founding + others_top)
            if keep_ids:
                placeholders = ",".join(str(i) for i in keep_ids)
                conn_purge.execute(f"DELETE FROM expertise WHERE id NOT IN ({placeholders})")
                conn_purge.commit()
            count_after = conn_purge.execute("SELECT COUNT(*) FROM expertise").fetchone()[0]
            log.info(f"🧹 Expertise purged: {count_before} → {count_after}")
        conn_purge.close()
    except Exception as ex_purge:
        log.error(f"⚠️ Purge expertise: {ex_purge}")

    # External purge removed (redundant with inline purge above)

    # ═══ STALE FILE KEYANUP ═══
    import glob as _glob
    _caduques = (
        _glob.glob(os.path.join(os.path.dirname(DB_PATH), "SPECIFICATION_v*.md")) +
        _glob.glob(os.path.join(os.path.dirname(DB_PATH), "diag_*.txt")) +
        _glob.glob(os.path.join(os.path.dirname(DB_PATH), "diag_*.json"))
    )
    for _f in _caduques:
        try:
            os.remove(_f)
            log.info(f"🗑️ Keyaned: {os.path.basename(_f)}")
        except Exception:
            pass

    # ═══ FIX DISHWASHER PLUG ROOM (kitchen) ═══
    try:
        conn_room = sqlite3.connect(DB_PATH)
        nb_fix = conn_room.execute(
            "UPDATE entity_map SET room='kitchen' WHERE entity_id LIKE '%lave_vaiselle%' AND (room IS NULL OR room='')"
        ).rowcount
        conn_room.commit()
        if nb_fix > 0:
            log.info(f"🏠 Dishwasher: {nb_fix} entity(ies) → kitchen")
        # Diagnostic: check all plugs
        plugs_without_room = conn_room.execute(
            "SELECT entity_id, friendly_name FROM entity_map WHERE category='connected_plug' AND (room IS NULL OR room='')"
        ).fetchall()
        if plugs_without_room:
            log.info(f"⚠️ {len(plugs_without_room)} plugs without room: {[r[0] for r in plugs_without_room]}")
        conn_room.close()
    except Exception as ex_lv:
        log.error(f"Fix dishwasher: {ex_lv}")

    # ═══ PURGE DUPLICATE HISTORICAL FAILURES (fix 20/03/2026) ═══
    try:
        conn_fix = sqlite3.connect(DB_PATH)
        # Keep only 1 copy of each historical FAILURE_, remove duplicates
        for failure_type in ["FAILURE_nas_false_positives", "FAILURE_printer_false_positives",
                           "FAILURE_silent_mode_spam", "FAILURE_entities_missing_entities_spam",
                           "FAILURE_shell_script_empty", "FAILURE_formula_solar",
                           "FAILURE_haiku_forgot_data", "FAILURE_budget_without_alert"]:
            ids = [r[0] for r in conn_fix.execute(
                "SELECT id FROM decisions_log WHERE action=? ORDER BY id ASC", (failure_type,)
            ).fetchall()]
            if len(ids) > 1:
                conn_fix.execute(
                    f"DELETE FROM decisions_log WHERE action=? AND id NOT IN ({ids[0]})",
                    (failure_type,)
                )
        conn_fix.commit()
        conn_fix.close()
        log.info("🧹 Duplicate historical failures purged")
    except Exception as ex_fix:
        log.error(f"Purge duplicates: {ex_fix}")

    # ═══ INJECT FOUNDING LESSONS (only once) ═══
    try:
        _injecter_lessons_founding(DB_PATH)
    except Exception as ex_lf:
        log.error(f"⚠️ Founding lessons: {ex_lf}")


def _injecter_lessons_founding(conn_or_path=None):
    """Injects lessons learned from failures — run only once at first startup.
    Each lesson is a documented failure + the rule derived from it."""
    import sqlite3 as _sq

    if isinstance(conn_or_path, str):
        conn = _sq.connect(conn_or_path)
    elif conn_or_path is None:
        conn = _sq.connect(DB_PATH)
    else:
        conn = conn_or_path

    # Check if already injected (LIKE to match founding_lesson:failure:...)
    already = conn.execute("SELECT COUNT(*) FROM expertise WHERE source LIKE 'founding_lesson%'").fetchone()[0]
    if already > 0:
        if isinstance(conn_or_path, str) or conn_or_path is None:
            conn.close()
        return  # Already done

    now_iso = datetime.now().isoformat()

    lessons = [
        # ═══ FAILURE 1: NAS false positives (13/03/2026) ═══
        ("monitoring",
         "NAS: NEVER monitor button.*, automation.*, switch.*, update.*, binary_sensor.* — only sensor.* matter",
         0.9, "failure:28_false_positives_nas_13mars"),
        ("monitoring",
         "NAS: a numeric state (temperature, disk sheet in TB) is NOT a degraded volume — verify it is text before alerting",
         0.9, "failure:temperature_26C_confondue_volume_degrade"),
        ("monitoring",
         "NAS: alert on disk sheet ONLY if unit=% AND value>90% — not on raw values in TB",
         0.9, "failure:1tb_sheet_false_positive"),
        ("monitoring",
         "NAS: the 'warning' status on a Synology volume IS a real alert — do not ignore it",
         0.9, "failure:vrai_positif_synology_warning"),

        # ═══ FAILURE 2: Printer false positives (13/03/2026) ═══
        ("monitoring",
         "Printer: OctoPrint is NOT the Brother printer — exclude octoprint/octopi/3d_print",
         0.95, "failure:octoprint_28_false_positives"),
        ("monitoring",
         "Printer: monitor ONLY sensor.* with unit % containing ink/toner/black/cyan/magenta/yellow",
         0.9, "failure:tous_domains_printer_alerts"),
        ("monitoring",
         "Printer: automation.* and button.* are NEVER physical sensors — always exclude",
         0.9, "failure:automations_alerted_as_offline"),

        # ═══ FAILURE 3: silent_mode heat pump (11/03/2026) ═══
        ("zigbee",
         "Zigbee: switch.*_silent_mode, *_powerful_mode, *_child_lock are logical heat pump sub-entities — unavailable is NORMAL",
         0.95, "failure:silent_mode_spam_permanent"),

        # ═══ FAILURE 4: Missing entities spam (13/03/2026) ═══
        ("monitoring",
         "Missing entities: ONE alert with buttons, then silence — never spam every 4h",
         0.9, "failure:spam_entities_missing_entities_4h"),

        # ═══ FAILURE 5: cmd_claude_autonomous shell script (11/03/2026) ═══
        ("code",
         "NEVER depend on an external shell script when Python can read the file directly",
         0.85, "failure:cmd_claude_shell_script_empty"),

        # ═══ FAILURE 6: Solar formula (11/03/2026) ═══
        ("energy",
         "Solar coverage = production / (grid + production) × 100 — Ecojoko = grid only, NOT total consumption",
         0.95, "failure:formula_solar_incorrecte"),

        # ═══ FAILURE 7: cmd_energy Haiku dependency (12/03/2026) ═══
        ("code",
         "Raw data reports must be structured in Python — DO NOT send to Haiku to summarize, it forgets data",
         0.9, "failure:haiku_forgot_heat_pump_dryer"),

        # ═══ FAILURE 8: Budget without alert (12/03/2026) ═══
        ("monitoring",
         "API budget: alert at deduplicated thresholds (50/80/90/100%) — do not let user discover an empty budget",
         0.85, "failure:budget_exhausted_without_alert"),

        # ═══ ARCHITECTURAL RULES ═══
        ("code",
         "Each entity_id pattern must be tested against the REAL HA entity_ids — never use too broad a pattern",
         0.8, "principe:patterns_precis"),
        ("code",
         "A domain (button, automation, switch, update) is NEVER a physical sensor — always filter by domain first",
         0.95, "principe:domains_no_physiques"),
        ("general",
         "Every alert must be deduplicated via _alert_if_new — NEVER repeat a raw alert",
         0.9, "principe:deduplication_alerts"),
        ("general",
         "Every error must be logged in decisions_log via learning_log_failure — not just in file logs",
         0.8, "principe:tracer_les_failures"),
        ("general",
         "Before monitoring a category, verify real entity_ids with /diag_carto — do not guess",
         0.85, "principe:check_before_coding"),
        ("monitoring",
         "Baselines: minimum 30 measurements before alerting — 10 measurements = too much noise, false positives guaranteed",
         0.9, "failure:baselines_10_samples_false_positives_14mars"),
        ("monitoring",
         "Solar production baselines: ignore if baseline < 50W, deviation threshold > 200% (clouds = normal variations)",
         0.9, "failure:baseline_solar_0w_night_alerted"),
        ("monitoring",
         "Solar production 0W: confirm on 2 consecutive cycles before alerting — one sensor glitch = false positive",
         0.85, "failure:solar_0w_false_positive_march14"),
        ("monitoring",
         "Offline entities threshold: 30% minimum (not 15%) — many entities are normally unavailable in HA",
         0.85, "failure:21pct_entities_unavailable_false_positive"),
        ("monitoring",
         "Extreme grid consumption: threshold 8000W (not 5000W) — oven + heat pump + machine = easily 5000W in normal use",
         0.8, "principe:seuil_consumption_realiste"),
        ("code",
         "Monitoring (monitoring + plugs) must run ALWAYS — never blocked by channel_locked or SMS code",
         0.95, "failure:dryer_no_detecte_channel_locked_14mars"),
        ("code",
         "channel_locked must block ONLY interactive Telegram commands — not background threads",
         0.95, "failure:monitoring_froste_apres_restart"),
    ]

    for cat, insight, conf, source in lessons:
        conn.execute(
            "INSERT OR IGNORE INTO expertise (category, insight, confidence, nb_validations, source, created_at, updated_at) "
            "VALUES (?, ?, ?, 1, ?, ?, ?)",
            (cat, insight, conf, f"founding_lesson:{source}", now_iso, now_iso)
        )

    # Guard: check if already injected (avoids duplicates on each restart)
    existing_failures = conn.execute(
        "SELECT COUNT(*) FROM decisions_log WHERE context LIKE '%history_fondateur%'"
    ).fetchone()[0]
    if existing_failures == 0:
        failures_historys = [
            ("FAILURE_nas_false_positives", "28 NAS false positives: button.*, automation.*, temperatures and raw disk sheet alerted as 'degraded volume'"),
            ("FAILURE_printer_false_positives", "28 printer false positives: OctoPrint + automations alerted as 'printer offline'"),
            ("FAILURE_silent_mode_spam", "switch.air_water_heat_pump_silent_mode was spamming 'Zigbee device offline'"),
            ("FAILURE_entities_missing_entities_spam", "5 deleted entities alerted every 4h endlessly"),
            ("FAILURE_shell_script_empty", "cmd_claude_autonomous called a shell script that returned empty output"),
            ("FAILURE_formula_solar", "Solar coverage calculated on grid only instead of grid+production"),
            ("FAILURE_haiku_forgot_data", "cmd_energy sent everything to Haiku which forgot heat pump and dryer"),
            ("FAILURE_budget_without_alert", "API budget exhausted with no prior alert"),
        ]
        for action, description in failures_historys:
            conn.execute(
                "INSERT INTO decisions_log (action, context, result, success, created_at) VALUES (?, ?, ?, 0, ?)",
                (action, '{"source": "history_fondateur"}', description, now_iso)
            )

    conn.commit()
    log.info(f"📕 {len(lessons)} founding lessons injected + {len(failures_historys)} historical failures")


def mem_set(key_name, value_text):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        'INSERT OR REPLACE INTO memory_store (key_name, value_text, updated_at) VALUES (?, ?, ?)',
        (key_name, str(value_text), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def mem_get(key_name, default=None):
    conn = sqlite3.connect(DB_PATH)
    r = conn.execute('SELECT value_text FROM memory_store WHERE key_name=?', (key_name,)).fetchone()
    conn.close()
    return r[0] if r else default


def log_token_usage(tokens_in, tokens_out):
    month = datetime.now().strftime("%Y-%m")
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        '''INSERT INTO tokens (month, tokens_in, tokens_out) VALUES (?, ?, ?)
           ON CONFLICT(month) DO UPDATE SET
           tokens_in = tokens_in + ?, tokens_out = tokens_out + ?''',
        (month, tokens_in, tokens_out, tokens_in, tokens_out)
    )
    conn.commit()
    conn.close()


def get_token_usage():
    month = datetime.now().strftime("%Y-%m")
    conn = sqlite3.connect(DB_PATH)
    r = conn.execute('SELECT tokens_in, tokens_out FROM tokens WHERE month=?', (month,)).fetchone()
    conn.close()
    return r if r else (0, 0)


def add_history(role, content):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        'INSERT INTO history (role, content, created_at) VALUES (?, ?, ?)',
        (role, content, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_history(n=6):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        'SELECT role, content FROM history ORDER BY id DESC LIMIT ?', (n,)
    ).fetchall()
    conn.close()
    return list(reversed(rows))


def entity_map_get(entity_id):
    conn = sqlite3.connect(DB_PATH)
    r = conn.execute(
        'SELECT category, subcategory, room FROM entity_map WHERE entity_id=?',
        (entity_id,)
    ).fetchone()
    conn.close()
    return r


def entity_map_get_by_category(category):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        'SELECT entity_id, subcategory, room FROM entity_map WHERE category=?',
        (category,)
    ).fetchall()
    conn.close()
    return rows


def entity_map_get_all_categories():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute('SELECT DISTINCT category FROM entity_map').fetchall()
    conn.close()
    return [r[0] for r in rows]


def entity_map_get_all():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute('SELECT entity_id, category FROM entity_map').fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def battery_set(entity_id, room, value_text):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        '''INSERT OR REPLACE INTO batteries
           (entity_id, room, last_value, updated_at)
           VALUES (?, ?, ?, ?)''',
        (entity_id, room, value_text, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def battery_get_last_alert(entity_id):
    conn = sqlite3.connect(DB_PATH)
    r = conn.execute(
        'SELECT last_alert FROM batteries WHERE entity_id=?', (entity_id,)
    ).fetchone()
    conn.close()
    return r[0] if r else None


def battery_set_alert(entity_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        'UPDATE batteries SET last_alert=? WHERE entity_id=?',
        (datetime.now().isoformat(), entity_id)
    )
    conn.commit()
    conn.close()


def role_get(role):
    """Returns the entity_id assigned to a role, or None"""
    conn = sqlite3.connect(DB_PATH)
    r = conn.execute("SELECT entity_id FROM roles WHERE role=?", (role,)).fetchone()
    conn.close()
    return r[0] if r else None


def role_set(role, entity_id, source="auto", confidence=0.5):
    """Assigns an entity_id to a role"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO roles (role, entity_id, confidence, source, updated_at) VALUES (?, ?, ?, ?, ?)",
        (role, entity_id, confidence, source, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    log.info(f"🎯 Role {role} → {entity_id} (confidence {confidence:.0%}, source: {source})")


def role_get_all():
    """Returns all assigned roles"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT role, entity_id, confidence FROM roles").fetchall()
    conn.close()
    return {r[0]: {"entity_id": r[1], "confidence": r[2]} for r in rows}


def role_val(role, states_index, default="?"):
    """Shortcut: returns the value of a role from the state index"""
    eid = role_get(role)
    if not eid:
        return default
    e = states_index.get(eid)
    if e and e["state"] not in ("unavailable", "unknown"):
        return e["state"]
    return default


def role_decouvrir(states):
    """Auto-discovery of roles — analyzes all HA entity_ids.
    Works on ANY HA installation."""
    import re as _re
    index = {e["entity_id"]: e for e in states}
    roles_currents = role_get_all()
    discovery_count = 0

    for role, definition in ROLES_DEFINIS.items():
        # If already assigned with high confidence, do not overwrite
        if role in roles_currents and roles_currents[role]["confidence"] >= 0.8:
            # Verify the entity still exists
            if roles_currents[role]["entity_id"] in index:
                continue

        desc = definition["description"]
        dc_cibles = definition.get("device_class", [])
        unit_cibles = definition.get("unit", [])
        patterns = definition.get("patterns", [])
        domain_cible = definition.get("domain", "sensor")

        best_candidate = None
        best_score = 0

        for eid, e in index.items():
            domain = eid.split(".")[0]

            # Filter by domain if specified
            if domain_cible and domain_cible != "sensor":
                if domain != domain_cible:
                    continue
            elif domain not in ("sensor",):
                continue

            attrs = e.get("attributes", {})
            dc = attrs.get("device_class", "")
            unit = attrs.get("unit_of_measurement", "")
            fname = attrs.get("friendly_name", "").lower()
            eid_low = eid.lower()

            score = 0

            # device_class score
            if dc_cibles and dc in dc_cibles:
                score += 3

            # unit score
            if unit_cibles and unit in unit_cibles:
                score += 2

            # entity_id or friendly_name pattern score
            pattern_match = False
            for pattern in patterns:
                if _re.search(pattern, eid_low) or _re.search(pattern, fname):
                    score += 4
                    pattern_match = True
                    break

            # exact domain score
            if domain_cible and domain == domain_cible:
                score += 1

            # Penalty if unavailable
            if e["state"] in ("unavailable", "unknown"):
                score -= 2

            # If patterns are defined, at least one must match
            # Otherwise score alone (device_class+unit) gives false positives
            if patterns and not pattern_match:
                continue

            if score > best_score:
                best_score = score
                best_candidate = eid

        if best_candidate and best_score >= 3:
            confidence = min(1.0, best_score / 10)
            role_set(role, best_candidate, "auto_discovery", confidence)
            discovery_count += 1

    if discovery_count > 0:
        log.info(f"🎯 {discovery_count} role(s) discovered")

    return discovery_count


def role_decouvrir_baselines():
    """Dynamically builds BASELINE_ENTITIES from discovered roles."""
    roles_baseline = {
        "realtime_consumption": "consumption_w",
        "solar_production_w": "production_w",
        "heat_pump_consumption": "consumption_heat_pump_w",
        "temp_interieure": "temp_int",
        "temp_exterieure": "temp_ext",
    }
    result = {}
    for role, label in roles_baseline.items():
        eid = role_get(role)
        if eid:
            result[eid] = label
    return result


def known_entities_maj(entity_id, category):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        '''INSERT OR REPLACE INTO known_entities
           (entity_id, category, last_seen)
           VALUES (?, ?, ?)''',
        (entity_id, category, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def known_entities_get_all():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute('SELECT entity_id, category FROM known_entities').fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def appliance_get(entity_id):
    """Returns the device type for a plug, or None if not yet identified."""
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT appliance_type, custom_name, monitored FROM appliances WHERE entity_id=?",
            (entity_id,)
        ).fetchone()
        conn.close()
        if row:
            return {"type": row[0], "name": row[1], "monitored": bool(row[2])}
    except Exception:
        pass
    return None


def appliance_set(entity_id, appliance_type, name=None):
    """Registers the device type for a plug."""
    monitored = 0 if appliance_type == "ignore" else 1
    if not name:
        name = APPLIANCE_TYPES.get(appliance_type, appliance_type)
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT OR REPLACE INTO appliances (entity_id, appliance_type, custom_name, monitored, created_at) VALUES (?, ?, ?, ?, ?)",
            (entity_id, appliance_type, name, monitored, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        log.info(f"🏷️ Device: {entity_id} → {appliance_type} ({name})")
    except Exception as e:
        log.error(f"appliance_set: {e}")


def enregistrer_saving(type_eco, description, euros, kwh=0, source="auto"):
    """Records a saving in the savings table + logs success."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO savings (type, description, euros, kwh_saved, source, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (type_eco, description, round(euros, 4), round(kwh, 4), source, datetime.now().isoformat())
        )
        conn.execute(
            "INSERT INTO decisions_log (action, context, result, success, created_at) VALUES (?, ?, ?, 1, ?)",
            ("ECONAMEIE", json.dumps({"type": type_eco, "eur": round(euros, 4)}, ensure_ascii=False),
             description[:100], datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"savings: {e}")


def get_savings_month(month=None):
    """Returns the total savings for a given month."""
    if not month:
        month = datetime.now().strftime("%Y-%m")
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT COALESCE(SUM(euros), 0), COALESCE(SUM(kwh_saved), 0), COUNT(*) "
            "FROM savings WHERE created_at LIKE ?",
            (f"{month}%",)
        ).fetchone()
        # By type
        types = conn.execute(
            "SELECT type, SUM(euros), COUNT(*) FROM savings WHERE created_at LIKE ? GROUP BY type",
            (f"{month}%",)
        ).fetchall()
        conn.close()
        return {
            "total_eur": row[0], "total_kwh": row[1], "nb_actions": row[2],
            "by_type": {t: {"eur": e, "nb": n} for t, e, n in types}
        }
    except Exception:
        return {"total_eur": 0, "total_kwh": 0, "nb_actions": 0, "by_type": {}}


def zigbee_absence_create(entity_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        '''INSERT OR REPLACE INTO zigbee_outages
           (entity_id, offline_since, status, alert_sent)
           VALUES (?, ?, 'pending', ?)''',
        (entity_id, datetime.now().isoformat(), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def zigbee_absence_get(entity_id):
    conn = sqlite3.connect(DB_PATH)
    r = conn.execute(
        'SELECT offline_since, status FROM zigbee_outages WHERE entity_id=? AND back_online IS NULL',
        (entity_id,)
    ).fetchone()
    conn.close()
    return r


def zigbee_absence_status(entity_id, status):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        'UPDATE zigbee_outages SET status=? WHERE entity_id=? AND back_online IS NULL',
        (status, entity_id)
    )
    conn.commit()
    conn.close()


def zigbee_absence_retour(entity_id):
    conn = sqlite3.connect(DB_PATH)
    r = conn.execute(
        'SELECT status FROM zigbee_outages WHERE entity_id=? AND back_online IS NULL',
        (entity_id,)
    ).fetchone()
    if r and r[0] == 'abnormal':
        conn.execute(
            'UPDATE zigbee_outages SET back_online=? WHERE entity_id=? AND back_online IS NULL',
            (datetime.now().isoformat(), entity_id)
        )
        conn.commit()
        conn.close()
        return True  # Signaler the retour
    conn.execute(
        'UPDATE zigbee_outages SET back_online=? WHERE entity_id=? AND back_online IS NULL',
        (datetime.now().isoformat(), entity_id)
    )
    conn.commit()
    conn.close()
    return False


def telegram_send(text, parse_mode=None, force=False):
    """Central point for ALL outgoing messages — LEARNING FILTER.
    Each message is validated, logged, and the filter improves over time.
    force=True for system messages (startup, SMS code) that bypass filters."""

    if not text or len(text.strip()) < 5:
        return None

    now_ts = datetime.now()
    text_lower = text.lower()
    filter_reason = None

    if not force:
        # ═══ FILTRE 1 : PATTERNS APPRIS (SQLite) ═══
        try:
            conn_f = sqlite3.connect(DB_PATH)
            patterns = conn_f.execute(
                "SELECT pattern, reason FROM message_filters WHERE active=1 AND action='block'"
            ).fetchall()
            conn_f.close()
            for pattern, reason in patterns:
                if pattern.lower() in text_lower:
                    filter_reason = f"pattern_learned:{reason}"
                    break
        except Exception:
            pass

        if not filter_reason:
            if "0 w" in text_lower or ": 0w" in text_lower:
                if any(k in text_lower for k in ["production", "solar", "slot", "reminder"]):
                    try:
                        h = now_ts.hour
                        if 9 <= h <= 17:
                            data_sol, nb_sol = skill_get("window_solar")
                            if data_sol and nb_sol >= 10:
                                j_str, h_str = str(now_ts.weekday()), str(h)
                                if j_str in data_sol and h_str in data_sol[j_str]:
                                    if data_sol[j_str][h_str][0] > 500:
                                        filter_reason = f"prod_0W_vs_skill_{int(data_sol[j_str][h_str][0])}W"
                    except Exception:
                        pass

        # ═══ FILTRE 3 : ANTI-DOUBLON (5 min) ═══
        if not filter_reason:
            if not hasattr(telegram_send, "_recent"):
                telegram_send._recent = []
            telegram_send._recent = [(t, m) for t, m in telegram_send._recent
                                      if (now_ts - t).total_seconds() < 300]
            for t, m in telegram_send._recent:
                if m == text:
                    filter_reason = "doublon_5min"
                    break

        # ═══ FILTRE 4 : ANTI-SPAM (50/day) ═══
        if not filter_reason:
            today_key = now_ts.strftime("%Y-%m-%d")
            if not hasattr(telegram_send, "_daily"):
                telegram_send._daily = {"date": today_key, "count": 0}
            if telegram_send._daily["date"] != today_key:
                telegram_send._daily = {"date": today_key, "count": 0}
            telegram_send._daily["count"] += 1
            if telegram_send._daily["count"] > 50:
                filter_reason = "limite_50_day"

    try:
        conn_log = sqlite3.connect(DB_PATH)
        conn_log.execute(
            "INSERT INTO message_log (message, sent, filter_reason, created_at) VALUES (?, ?, ?, ?)",
            (text[:500], 0 if filter_reason else 1, filter_reason, now_ts.isoformat())
        )
        conn_log.execute("DELETE FROM message_log WHERE id NOT IN (SELECT id FROM message_log ORDER BY id DESC LIMIT 500)")
        conn_log.commit()
        conn_log.close()
    except Exception:
        pass

    if filter_reason:
        log.warning(f"🚫 Filtered [{filter_reason}]: {text[:80]}")
        return None

    # ═══ ENVOI ═══
    if not hasattr(telegram_send, "_recent"):
        telegram_send._recent = []
    telegram_send._recent.append((now_ts, text))

    url = f"https://api.telegram.org/bot{CFG['telegram_token']}/sendMessage"
    payload_tg = {"chat_id": CFG["telegram_chat_id"], "text": text}
    if parse_mode:
        payload_tg["parse_mode"] = parse_mode
    try:
        r = requests.post(url, json=payload_tg, timeout=10)
        if r.status_code == 200:
            count = getattr(telegram_send, "_daily", {}).get("count", "?")
            log.debug(f"📨 [{count}/50]: {text[:80]}")
            return r.json().get("result", {}).get("message_id")
        log.error(f"❌ Telegram {r.status_code}: {r.text[:100]}")
    except Exception as e:
        log.error(f"❌ Telegram: {e}")
    return None


def filter_apprendre_pattern(pattern, reason, action="block"):
    """L'IA apprend a new pattern of filtrage"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT OR REPLACE INTO message_filters (pattern, action, reason, applied_count, active, created_at, updated_at) "
            "VALUES (?, ?, ?, 0, 1, ?, ?)",
            (pattern, action, reason, datetime.now().isoformat(), datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        log.info(f"🧠 Filter learned: '{pattern}' → {action} ({reason})")
    except Exception:
        pass


def filter_analyze_messages():
    """Analysis the messages sents and filters for apprendre of nouveaux patterns.
    Called every 12h by the intelligence engine."""
    call_claude._search_count = 0
    if not check_budget():
        return

    conn = sqlite3.connect(DB_PATH)

    filters = conn.execute(
        "SELECT filter_reason, COUNT(*) as nb FROM message_log "
        "WHERE sent=0 AND filter_reason IS NOT NULL "
        "GROUP BY filter_reason ORDER BY nb DESC LIMIT 10"
    ).fetchall()

    sents = conn.execute(
        "SELECT message, created_at FROM message_log "
        "WHERE sent=1 ORDER BY id DESC LIMIT 30"
    ).fetchall()

    # Patterns existants
    patterns_actives = conn.execute(
        "SELECT pattern, applied_count FROM message_filters WHERE active=1"
    ).fetchall()

    conn.close()

    if len(sents) < 10:
        return  # Pas assez of data

    prefixes = {}
    for msg, dt in sents:
        prefix = msg[:40]
        prefixes[prefix] = prefixes.get(prefix, 0) + 1

    repetitifs = [(p, n) for p, n in prefixes.items() if n >= 3]
    if repetitifs:
        prompt = (
            "You are the noise filter of the home assistant.\n"
            "Here are frequently sent Telegram messages:\n"
        )
        for prefix, nb in repetitifs[:5]:
            prompt += f"  {nb}x : {prefix}...\n"
        prompt += (
            "\nAre these messages NOISE (repetitive, useless) or LEGITIMATE?\n"
            "Reply in JSON: {\"noise_patterns\": [\"pattern to filter\"], "
            "\"ok_patterns\": [\"legitimate pattern\"]}\n"
            "Just the JSON, nothing else."
        )

        try:
            blocks, t_in, t_out = llm_provider.llm_completion(
                CFG, [{"role": "user", "content": prompt}],
                max_tokens=300
            )
            log_token_usage(t_in, t_out)
            text = llm_provider.stream_text(blocks).strip().replace("```json", "").replace("```", "").strip()
            result = json.loads(text)

            for pattern in result.get("patterns_bruit", []):
                if pattern and len(pattern) >= 10:
                    filter_apprendre_pattern(pattern, "detecte_auto_repetitif")

            log.info(f"🧠 Analysis filter: {len(result.get('patterns_bruit', []))} nouveaux patterns")
        except Exception as ex:
            log.error(f"❌ filter_analyze: {ex}")


def telegram_send_buttons(text, buttons, action_data=None):
    url = f"https://api.telegram.org/bot{CFG['telegram_token']}/sendMessage"
    keyboard = []
    line = []
    for i, b in enumerate(buttons):
        line.append({"text": b["text"], "callback_data": b["callback_data"]})
        if len(line) == 3:
            keyboard.append(line)
            line = []
    if line:
        keyboard.append(line)
    payload = {
        "chat_id": CFG["telegram_chat_id"],
        "text": text,
        "reply_markup": {"inline_keyboard": keyboard}
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            msg_id = r.json().get("result", {}).get("message_id")
            if action_data and msg_id:
                pending_response[msg_id] = action_data
            return msg_id
        log.error(f"❌ Buttons Telegram {r.status_code}")
    except Exception as e:
        log.error(f"❌ Buttons Telegram: {e}")
    return None


def telegram_send_photo(image_bytes, caption=""):
    """Send an image on Telegram with sendPhoto."""
    try:
        import io
        url = f"https://api.telegram.org/bot{CFG['telegram_token']}/sendPhoto"
        files = {"photo": ("graph.png", io.BytesIO(image_bytes), "image/png")}
        data = {"chat_id": CFG["telegram_chat_id"]}
        if caption:
            data["caption"] = caption[:1024]
        r = requests.post(url, files=files, data=data, timeout=30)
        if r.status_code == 200:
            return True
        log.error(f"sendPhoto: {r.status_code}")
    except Exception as e:
        log.error(f"sendPhoto: {e}")
    return False


def telegram_answer_callback(callback_query_id, text="✅"):
    url = f"https://api.telegram.org/bot{CFG['telegram_token']}/answerCallbackQuery"
    try:
        requests.post(url, json={"callback_query_id": callback_query_id, "text": text}, timeout=5)
    except Exception:
        pass


def telegram_get_updates(offset=None):
    url = f"https://api.telegram.org/bot{CFG['telegram_token']}/getUpdates"
    params = {"timeout": 5}
    if offset:
        params["offset"] = offset
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            return r.json().get("result", [])
    except Exception as e:
        log.error(f"❌ Polling Telegram: {e}")
    return []


def generer_code_auth():
    global code_auth
    code_auth = str(random.randint(100000, 999999))
    return code_auth


def send_code_sms():
    """Send the security code through the configured channel.
    Methodes supportees (priorite) :
    1. Free Mobile API (sms_method=free_mobile)
    2. Notification HA Companion (sms_method=ha_notify)
    3. Email (sms_method=email)
    """
    global code_auth
    code = generer_code_auth()
    if not code or code == "None" or not code.isdigit() or len(code) != 6:
        log.error(f"Invalid SMS code: '{code}' — forced regeneration")
        code = str(random.randint(100000, 999999))
        code_auth = code
    log.info(f"SMS code generated: {code} (method={CFG.get('sms_method', '?')})")
    method = CFG.get("sms_method", "free_mobile")

    # ═══ FREE MOBILE API ═══
    if method == "free_mobile":
        user = CFG.get("free_mobile_user", "")
        passwd = CFG.get("free_mobile_pass", "")
        if user and passwd:
            try:
                url = f"https://smsapi.free-mobile.fr/sendmsg?user={user}&pass={passwd}&msg=CodeIA:{code}"
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    log.info(f"✅ SMS Free sent: {code}")
                    return True
                log.error(f"❌ SMS Free {r.status_code}")
            except Exception as e:
                log.error(f"❌ SMS Free: {e}")

    # ═══ NOTIFICATION HA COMPANION ═══
    elif method == "ha_notify":
        notify_service = CFG.get("ha_notify_service", "")
        if notify_service and CFG.get("ha_url") and CFG.get("ha_token"):
            try:
                url = f"{CFG['ha_url']}/api/services/notify/{notify_service}"
                headers = {"Authorization": f"Bearer {CFG['ha_token']}", "Content-Type": "application/json"}
                payload = {
                    "title": "🔐 AI Companion — Security code",
                    "message": f"Your code: {code}",
                    "data": {"priority": "high", "ttl": 0}
                }
                r = requests.post(url, json=payload, headers=headers, verify=False, timeout=10)
                if r.status_code == 200:
                    log.info(f"✅ HA notification sent: {code}")
                    return True
                log.error(f"❌ Notify HA {r.status_code}")
            except Exception as e:
                log.error(f"❌ Notify HA: {e}")

    # ═══ EMAIL ═══
    elif method == "email":
        email = CFG.get("email_dest", "")
        if email and CFG.get("smtp_host"):
            try:
                msg_email = MIMEText(f"Your AI Companion security code: {code}")
                msg_email["Subject"] = f"🔐 Code AI Companion : {code}"
                msg_email["From"] = CFG.get("smtp_user", "")
                msg_email["To"] = email
                with smtplib.SMTP(CFG["smtp_host"], CFG.get("smtp_port", 587)) as s:
                    s.starttls()
                    s.login(CFG["smtp_user"], CFG["smtp_pass"])
                    s.send_message(msg_email)
                log.info(f"✅ Email code sent: {code}")
                return True
            except Exception as e:
                log.error(f"❌ Email code: {e}")

    log.error(f"❌ No SMS method configured (method={method})")
    return False


def check_code(message):
    global channel_locked, code_auth
    if message.strip() == code_auth:
        channel_locked = False
        code_auth = None
        mem_set("last_unlock", datetime.now().isoformat())
        log.info("✅ Channel unlocked + saved (24h without SMS)")
        return True
    return False


def ha_get(endpoint, _retries=2, _delay=5):
    """GET HA API with automatic retry on transient grid errors."""
    if not CFG.get("ha_url"):
        return None
    url = f"{CFG['ha_url']}/api/{endpoint}"
    headers = {"Authorization": f"Bearer {CFG['ha_token']}"}
    for attempt in range(_retries + 1):
        try:
            r = requests.get(url, headers=headers, verify=False, timeout=15)
            if r.status_code == 200:
                return r.json()
            log.warning(f"⚠️ HA {endpoint}: HTTP {r.status_code}")
            return None
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt < _retries:
                import time as _t; _t.sleep(_delay * (attempt + 1))
                continue
            log.warning(f"⚠️ HA {endpoint}: network unavailable after {_retries+1} attempts")
        except Exception as e:
            log.error(f"❌ HA {endpoint}: {e}")
            break
    return None


def ha_post(endpoint, data):
    url = f"{CFG['ha_url']}/api/{endpoint}"
    headers = {"Authorization": f"Bearer {CFG['ha_token']}", "Content-Type": "application/json"}
    try:
        r = requests.post(url, headers=headers, json=data, verify=False, timeout=15)
        return r.json()
    except Exception as e:
        log.error(f"❌ HA POST {endpoint}: {e}")
    return None


def ha_get_forecast(entity_id=None, forecast_type="daily"):
    if entity_id is None:
        entity_id = role_get("weather") or "weather.pavillons_sous_bois"
    """Retrieves the forecast weather via the service weather.get_forecasts (HA 2024+)"""
    try:
        url = f"{CFG['ha_url']}/api/services/weather/get_forecasts?return_response"
        headers = {"Authorization": f"Bearer {CFG['ha_token']}", "Content-Type": "application/json"}
        data = {"entity_id": entity_id, "type": forecast_type}
        r = requests.post(url, headers=headers, json=data, verify=False, timeout=15)
        if r.status_code == 200:
            result = r.json()
            # Format : {"service_response": {"weather.xxx": {"forecast": [...]}}}
            service_resp = result.get("service_response", result)
            if isinstance(service_resp, dict):
                entity_data = service_resp.get(entity_id, {})
                if isinstance(entity_data, dict):
                    return entity_data.get("forecast", [])
            # Fallback direct
            if isinstance(result, dict) and entity_id in result:
                return result[entity_id].get("forecast", [])
        else:
            log.debug(f"⚠️ Forecast HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.debug(f"⚠️ Forecast {entity_id}: {e}")
    return []


def ha_get_state(entity_id):
    """Retrieves the state of a specific entity"""
    return ha_get(f"states/{entity_id}")


def ha_est_day(states):
    """Returns True between sunrise and sunset."""
    index = {e["entity_id"]: e for e in states}

    # Source 1 : sun.sun
    sun = index.get("sun.sun")
    if sun:
        return sun["state"] == "above_horizon"

    # Source 2 : weather.*
    for e in states:
        if e["entity_id"].startswith("weather."):
            attrs = e.get("attributes", {})
            sunrise = attrs.get("sunrise") or attrs.get("next_rising")
            sunset  = attrs.get("sunset")  or attrs.get("next_setting")
            if sunrise and sunset:
                try:
                    now = datetime.now(timezone.utc)
                    sr = datetime.fromisoformat(sunrise.replace("Z", "+00:00"))
                    ss = datetime.fromisoformat(sunset.replace("Z", "+00:00"))
                    return sr <= now <= ss
                except Exception:
                    pass

    return True


def ha_get_current_solar_production(states):
    """Returns total instantaneous solar power in W.
    Sources : ECU Current Power (APSystems) + Plug Anker (injection Solarbank).
    Returns 0 if no solar sensors are installed."""
    if not role_get("solar_production_w"):
        return 0
    if not ha_est_day(states):
        return 0

    index = {e["entity_id"]: e for e in states}
    total_w = 0

    eid_aps = role_get("solar_production_w") or "sensor.ecu_current_power"
    e_aps = index.get(eid_aps)
    if e_aps and e_aps["state"] not in ("unavailable", "unknown"):
        try:
            val = float(e_aps["state"])
            if 0 <= val <= 20000:
                total_w += val
        except Exception:
            pass

    # Source 2 : Anker Solarbank injection (plug Anker W)
    eid_anker = role_get("battery_power")
    if eid_anker:
        e_anker = index.get(eid_anker)
        if e_anker and e_anker["state"] not in ("unavailable", "unknown"):
            try:
                val = float(e_anker["state"])
                if 0 <= val <= 5000:
                    total_w += val
            except Exception:
                pass

    return total_w


def _alert_if_new(key_name, message, delay_h=2):
    """Send an alert only if it was not sent recently"""
    last = mem_get(f"alert_{key_name}")
    if last:
        try:
            delta = (datetime.now() - datetime.fromisoformat(last)).total_seconds()
            if delta < delay_h * 3600:
                return
        except Exception:
            pass
    telegram_send(message)
    mem_set(f"alert_{key_name}", datetime.now().isoformat())
    log.warning(f"Alert: {message[:80]}")


def skill_get(name):
    """Lit a skill since SQLite"""
    conn = sqlite3.connect(DB_PATH)
    r = conn.execute("SELECT data, learning_count FROM skills WHERE name=?", (name,)).fetchone()
    conn.close()
    if r:
        return json.loads(r[0]), r[1]
    return {}, 0


def skill_set(name, data, nb=None):
    """Ecrit a skill in SQLite"""
    conn = sqlite3.connect(DB_PATH)
    old = conn.execute("SELECT learning_count FROM skills WHERE name=?", (name,)).fetchone()
    nb_val = nb if nb is not None else ((old[0] + 1) if old else 1)
    conn.execute(
        "INSERT OR REPLACE INTO skills (name, data, learning_count, updated_at) VALUES (?, ?, ?, ?)",
        (name, json.dumps(data, ensure_ascii=False), nb_val, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def rate_get():
    """Returns the user rate configuration"""
    data, nb = skill_get("pricing")
    if data and "type" in data:
        return data
    return DEFAULT_RATES["base"]  # Default if not configured


def _est_hour_creuse_ranges(off_peak_hours):
    """Checks whether the current time is in an off-peak range"""
    now = datetime.now()
    current_minute = now.hour * 60 + now.minute
    for range in off_peak_hours:
        try:
            started_at_str, ended_at_str = range.split("-")
            dh, dm = map(int, started_at_str.split(":"))
            fh, fm = map(int, ended_at_str.split(":"))
            started_at_min = dh * 60 + dm
            ended_at_min = fh * 60 + fm
            if started_at_min > ended_at_min:
                if current_minute >= started_at_min or current_minute < ended_at_min:
                    return True
            else:
                if started_at_min <= current_minute < ended_at_min:
                    return True
        except Exception:
            pass
    return False


def _est_weekend_ou_ferie():
    """Checks whether today is a weekend or holiday"""
    now = datetime.now()
    if now.weekday() >= 5:  # Samedi=5, Dimanche=6
        return True
    # French public holidays
    y = now.year
    holidays = [
        (1, 1), (5, 1), (5, 8), (7, 14), (8, 15),
        (11, 1), (11, 11), (12, 25),
    ]
    if (now.month, now.day) in holidays:
        return True
    return False


def _est_chosen_day(rate):
    """Checks whether today is the selected day (Week-End Plus)"""
    chosen_day = rate.get("chosen_day")
    if chosen_day is not None:
        return datetime.now().weekday() == chosen_day
    return False


def rate_current_kwh_price():
    """Returns the current kWh price based on offer type, day, and time"""
    rate = rate_get()
    ttype = rate.get("type", "base")

    if ttype == "base":
        return rate.get("price_kwh", 0.2516)

    if ttype == "hphc":
        hc = _est_hour_creuse_ranges(rate.get("off_peak_hours", []))
        return rate.get("price_hc" if hc else "price_hp", 0.25)

    if ttype == "weekend":
        if _est_weekend_ou_ferie():
            return rate.get("price_weekend", 0.1538)
        return rate.get("price_weekday", 0.2038)

    if ttype == "weekend_hphc":
        if _est_weekend_ou_ferie():
            return rate.get("price_weekend", 0.1618)
        hc = _est_hour_creuse_ranges(rate.get("off_peak_hours", []))
        return rate.get("price_hc" if hc else "price_hp_weekday", 0.2153)

    if ttype == "weekend_plus":
        if _est_weekend_ou_ferie() or _est_chosen_day(rate):
            return rate.get("price_weekend_day", 0.1604)
        return rate.get("price_weekday", 0.2133)

    if ttype == "weekend_plus_hphc":
        if _est_weekend_ou_ferie() or _est_chosen_day(rate):
            return rate.get("price_hc_weekend_day", 0.166)
        hc = _est_hour_creuse_ranges(rate.get("off_peak_hours", []))
        if hc:
            return rate.get("price_hc_weekend_day", 0.166)
        return rate.get("price_hp_weekday", 0.2213)

    if ttype == "tempo":
        hc = _est_hour_creuse_ranges(rate.get("off_peak_hours", []))
        return rate.get("price_blue_hc" if hc else "price_blue_hp", 0.12)

    return 0.2516


def rate_est_hour_creuse():
    """Returns True during off-peak hours"""
    rate = rate_get()
    if rate.get("type") != "hphc":
        return False
    price_hc = rate.get("price_hc", 0.2068)
    return rate_current_kwh_price() == price_hc


def send_email(subject, body, attachment=None):
    try:
        msg = MIMEMultipart()
        msg["From"] = CFG["smtp_user"]
        msg["To"]   = CFG["email_dest"]
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))
        if attachment and os.path.exists(attachment):
            with open(attachment, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(attachment)}")
            msg.attach(part)
        s = smtplib.SMTP(CFG["smtp_host"], CFG["smtp_port"])
        s.starttls()
        s.login(CFG["smtp_user"], CFG["smtp_pass"])
        s.sendmail(CFG["smtp_user"], CFG["email_dest"], msg.as_string())
        s.quit()
        log.info(f"✅ Email: {subject}")
        return True
    except Exception as e:
        log.error(f"❌ Email: {e}")
        return False


def _wizard_step():
    """Returns the current wizard step, or None when finished."""
    return CFG.get("_wizard_step")


def _wizard_save_config():
    """Sauvegarde the config pendant the wizard."""
    with open(CONFIG_PATH, "w") as f:
        json.dump(CFG, f, indent=2)


def load_behavior_prompt():
    if os.path.exists(BEHAVIOR_PROMPT):
        with open(BEHAVIOR_PROMPT) as f:
            return f.read()
    return """You are the user's home automation assistant.
You respond in English, concisely and professionally.
You monitor the house and alert only on real problems.
Priorities: energy (heat pump, solar, plugs), Zigbee, NAS, network.

HOME ASSISTANT ACTIONS:
You have the ha_call_service tool to act on devices.
When the user requests an action, use the tool DIRECTLY without asking questions.
NEVER ask for textual confirmation — the system handles confirmation via buttons.
DO NOT say you don't have access to HA — you have real access via the tool.
ALWAYS use the exact entity_id visible in the HA state.
Be CONCISE: no markdown, no code blocks, just the action.

ABSOLUTE RULES:
- climate auto/heat/cool/fan_only = HEAT PUMP ACTIVE
- climate off = HEAT PUMP OFF
- Ingreenrs 0W at night = NORMAL
- Anker battery < 20% = report
- Automations unavailable = normal"""


def check_budget():
    tokens_in, tokens_out = get_token_usage()
    cost = (tokens_in * 0.000001) + (tokens_out * 0.000005)
    budget = CFG.get("llm_monthly_budget_usd", CFG.get("anthropic_monthly_budget_usd", 10))
    pct = (cost / budget * 100) if budget > 0 else 0

    if pct >= 100:
        _alert_if_new(
            "budget_100",
            f"🛑 API BUDGET EXCEEDED — ${cost:.2f} / ${budget} ({pct:.0f}%)\n"
            f"AI commands are disabled until the 1st of the month.\n"
            f"Local commands (/budget /debug /logs /batteries etc.) remain active.",
            delay_h=12
        )
        return False
    elif pct >= 90:
        _alert_if_new(
            "budget_90",
            f"🚨 API BUDGET 90% — ${cost:.2f} / ${budget}\n~${(budget - cost):.2f} remaining this month.",
            delay_h=12
        )
    elif pct >= 80:
        _alert_if_new(
            "budget_80",
            f"⚠️ API BUDGET 80% — ${cost:.2f} / ${budget}",
            delay_h=24
        )
    elif pct >= 50:
        _alert_if_new(
            "budget_50",
            f"📊 API budget 50% — ${cost:.2f} / ${budget}",
            delay_h=48
        )
    return True


def _appel_api_avec_retry(cfg, messages, model, max_tokens, system_prompt=None, tools=None, temperature=0):
    """Unified LLM call with retry backoff on 429. CRASH-PROOF."""
    for tentative in range(4):
        try:
            if tools:
                return llm_provider.llm_completion_with_tools(cfg, messages, tools, model=model, max_tokens=max_tokens, system_prompt=system_prompt, temperature=temperature)
            else:
                return llm_provider.llm_completion(cfg, messages, model=model, max_tokens=max_tokens, system_prompt=system_prompt, temperature=temperature)
        except Exception:
            wait = (tentative + 1) * 15
            log.warning(f"LLM API: retry in {wait}s (attempt {tentative + 1}/4)")
            time.sleep(wait)
    log.error("LLM API: 4 attempts failed")
    return None, 0, 0


def call_claude(user_message, context_ha=None):
    call_claude._search_count = 0
    if not check_budget():
        return "⚠️ Budget API monthly atteint."

    behavior_prompt = load_behavior_prompt()
    # Instructions autonomous
    system_prompt = behavior_prompt + """
REGLES CRITIQUES :
- You have access to ALL HA entities in the state below. FIND the entity_ids yourself.
- NEVER ask the user to find entities — you have the data.
- For a simple action (turn on/off), use ha_call_service DIRECTLY.
- For monitoring, use ha_create_watch directly.
- You are AUTONAMEOUS: the user gives the goal, you find the means.

AUTOMATIONS — MANDATORY METHOD:
1. First, use ha_search_entities to find entities related to the request
2. With the results, use ha_create_automation to create the automation
3. The system shows a summary with Validate/Modify/Cancel buttons
- NEVER ASK QUESTIONS. NEVER ASK FOR CLARIFICATION. ACT.
- Works with ALL HA integrations: Anker, Shelly, Zigbee, Tuya, etc.
"""
    if context_ha:
        system_prompt += f"\n\n=== HOME ASSISTANT STATE ===\n{context_ha}"

    history = get_history(6)
    messages = [{"role": r, "content": c} for r, c in history]
    messages.append({"role": "user", "content": user_message})

    try:
        # Determine if strong model is needed for complex requests
        _kw = ["automation", "automation", "create a", "create", "configure",
            "load shedding", "scenario", "routine", "when the battery",
            "if the temperature", "program a", "yaml", "script ha",
            "problem", "fix", "patch", "auto-heal"]
        _use_strong = any(k in user_message.lower() for k in _kw)
        _model = llm_provider.get_model(CFG, use_strong=_use_strong)

        blocks, t_in, t_out = llm_provider.llm_completion_with_tools(
            CFG, messages, HA_TOOLS, model=_model,
            max_tokens=2000 if _use_strong else 1000,
            system_prompt=system_prompt
        )
        if blocks is None:
            return "⚠️ The AI API is not responding. Please check your configuration."

        log_token_usage(t_in, t_out)
        log.debug(f"Tokens: in={t_in} out={t_out}")

        blocks = llm_provider.dictify_content_blocks(blocks)

        text_response = ""
        requested_action = None
        watch_demandee = None
        automation_demandee = None
        for block in blocks:
            if block["type"] == "text":
                text_response += block.get("text", "")
            elif block["type"] == "tool_use" and block["name"] == "ha_call_service":
                requested_action = block["input"]
            elif block["type"] == "tool_use" and block["name"] == "ha_search_entities":
                _search_count = getattr(call_claude, '_search_count', 0) + 1
                if _search_count > 2:
                    log.warning(f"⚠️ Search loop detected ({_search_count} calls) — stopping")
                    text_response = "I could not find the necessary entities. Please rephrase your request."
                    break
                call_claude._search_count = _search_count
                search_input = block["input"]
                keyword = search_input.get("keyword", "").lower()
                domain_filter = search_input.get("domain", "")
                # Search in HA
                try:
                    all_states = ha_get("states") or []
                    results = []
                    for e in all_states:
                        eid = e["entity_id"]
                        fname = e.get("attributes", {}).get("friendly_name", "")
                        state = e.get("state", "")
                        if keyword in eid.lower() or keyword in fname.lower():
                            if domain_filter and not eid.startswith(domain_filter + "."):
                                continue
                            attrs = e.get("attributes", {})
                            info = f"{eid} = {state}"
                            if fname:
                                info += f" ({fname})"
                            # Add useful attributes
                            for k in ["unit_of_measurement", "device_class", "min", "max", "options"]:
                                if k in attrs:
                                    info += f" [{k}={attrs[k]}]"
                            results.append(info)
                    search_result = f"Results for '{keyword}':\n" + "\n".join(results[:30]) if results else f"No entity found for '{keyword}'"
                    messages_suite = messages + [
                        {"role": "assistant", "content": [{"type": "tool_use", "id": block["id"], "name": "ha_search_entities", "input": search_input}]},
                        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": block["id"], "content": search_result}]}
                    ]
                    blocks2, t_in2, t_out2 = llm_provider.llm_completion_with_tools(
                        CFG, messages_suite, HA_TOOLS, model=_model,
                        max_tokens=2000 if _use_strong else 1000,
                        system_prompt=system_prompt
                    )
                    log_token_usage(t_in2, t_out2)
                    if blocks2:
                        blocks2 = llm_provider.dictify_content_blocks(blocks2)
                        for block2 in blocks2:
                            if block2["type"] == "text":
                                text_response += block2.get("text", "")
                            elif block2["type"] == "tool_use" and block2["name"] == "ha_call_service":
                                requested_action = block2["input"]
                            elif block2["type"] == "tool_use" and block2["name"] == "ha_create_automation":
                                automation_demandee = block2["input"]
                            elif block2["type"] == "tool_use" and block2["name"] == "ha_create_watch":
                                watch_demandee = block2["input"]
                except Exception as e:
                    log.error(f"Search entities: {e}")
                    text_response = f"Search error: {e}"
            elif block["type"] == "tool_use" and block["name"] == "ha_create_automation":
                automation_demandee = block["input"]
            elif block["type"] == "tool_use" and block["name"] == "ha_create_watch":
                watch_demandee = block["input"]

        add_history("user", user_message)

        if requested_action:
            domain = requested_action.get("domain", "")
            service = requested_action.get("service", "")
            entity_id = requested_action.get("entity_id", "")
            data = requested_action.get("data", {})

            if domain not in HA_DOMAINES_AUTORISES:
                msg = f"❌ Domain '{domain}' not authorized."
                add_history("assistant", msg)
                return msg

            # Store the pending action
            action_json = json.dumps({
                "domain": domain, "service": service,
                "entity_id": entity_id, "data": data
            })
            mem_set("ha_action_pending", action_json)

            # Human-readable action description
            LABELS = {
                "turn_on": "Turn on", "turn_off": "Turn off", "toggle": "Toggle",
                "lock": "Lock", "unlock": "Unlock",
                "open_cover": "Open", "close_cover": "Close", "stop_cover": "Stop",
                "set_temperature": "Set temperature", "set_hvac_mode": "Change mode",
                "start": "Start", "stop": "Stop", "return_to_base": "Return to base",
                "media_play": "Play", "media_pause": "Pause", "volume_set": "Volume",
            }
            action_label = LABELS.get(service, service)
            entity_short = entity_id.split(".", 1)[1].replace("_", " ").title() if "." in entity_id else entity_id

            confirm_msg = f"🔧 ACTION REQUESTED\n━━━━━━━━━━━━━━━━━━\n"
            confirm_msg += f"{action_label} → {entity_short}\n"
            confirm_msg += f"({domain}.{service} on {entity_id})"
            if data:
                confirm_msg += f"\nParameters: {json.dumps(data)}"

            buttons = [
                {"text": "✅ Confirm", "callback_data": "ha_action:confirm"},
                {"text": "❌ Cancel", "callback_data": "ha_action:cancel"},
            ]
            telegram_send_buttons(confirm_msg, buttons)
            add_history("assistant", f"[ACTION] {action_label} {entity_short}")
            return ""

        if watch_demandee:
            try:
                pattern = watch_demandee.get("entity_pattern", "")
                condition = watch_demandee.get("condition", "")
                state_value = watch_demandee.get("state_value", "")
                message = watch_demandee.get("message", "")
                cooldown = watch_demandee.get("cooldown_min", 60)

                conn = sqlite3.connect(DB_PATH)
                conn.execute(
                    "INSERT INTO watches (entity_pattern, condition, state_value, message, cooldown_min, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (pattern, condition, state_value, message, cooldown, datetime.now().isoformat())
                )
                conn.commit()
                conn.close()

                confirm = f"✅ Alert created\n━━━━━━━━━━━━━━━━━━\n"
                confirm += f"📡 Watching: {pattern}\n"
                confirm += f"🔍 Condition: {condition}"
                if state_value:
                    confirm += f" {state_value}"
                confirm += f"\n💬 Message: {message}\n"
                confirm += f"⏱️ Cooldown: {cooldown} min"
                telegram_send(confirm)
                add_history("assistant", f"[WATCH] {pattern} → {condition}")
                log.info(f"✅ Watch created: {pattern} {condition} {state_value}")
                return ""
            except Exception as e:
                log.error(f"❌ Watch creation: {e}")
                return f"❌ Alert creation error: {str(e)[:100]}"

        if automation_demandee:
            try:
                alias = automation_demandee.get("alias", "AI Companion Auto")
                auto_data = {
                    "alias": alias,
                    "description": automation_demandee.get("description", "Created by AI Companion"),
                    "trigger": automation_demandee.get("trigger", []),
                    "condition": automation_demandee.get("condition", []),
                    "action": automation_demandee.get("action", []),
                    "mode": automation_demandee.get("mode", "single"),
                }
                # Cancel any previous pending automation
                mem_set("ha_automation_pending", "")
                mem_set("ha_automation_pending", json.dumps(auto_data))

                TRAD_PLATFORM = {"numeric_state": "When the value of", "state": "When the state of"}
                TRAD_SERVICE = {
                    "switch.turn_on": "Activate", "switch.turn_off": "Deactivate",
                    "light.turn_on": "Turn on", "light.turn_off": "Turn off",
                    "cover.open_cover": "Open", "cover.close_cover": "Close",
                    "climate.set_temperature": "Set temperature",
                    "number.set_value": "Set value of",
                    "input_number.set_value": "Set",
                }

                msg = f"📋 PROPOSED AUTOMATION\n━━━━━━━━━━━━━━━━━━\n"
                msg += f"📝 {alias}\n\n"
                msg += "📌 Triggers:\n"
                for t in auto_data.get("trigger", []):
                    platform = t.get("platform", "")
                    eid = t.get("entity_id", "")
                    eid_short = eid.split(".")[-1].replace("_", " ").title() if eid else ""
                    above = t.get("above", "")
                    below = t.get("below", "")
                    to_state = t.get("to", "")
                    trad = TRAD_PLATFORM.get(platform, platform)
                    if above:
                        msg += f"  • {trad} {eid_short} exceeds {above}\n"
                    elif below:
                        msg += f"  • {trad} {eid_short} drops below {below}\n"
                    elif to_state:
                        msg += f"  • {trad} {eid_short} changes to {to_state}\n"
                    else:
                        msg += f"  • {trad} {eid_short} changes\n"

                def _format_action(a):
                    lines = []
                    if "service" in a:
                        svc = a["service"]
                        target = a.get("target", {})
                        eid_a = target.get("entity_id", a.get("entity_id", "")) if isinstance(target, dict) else str(target)
                        eid_short = eid_a.split(".")[-1].replace("_", " ").title() if eid_a else ""
                        svc_trad = TRAD_SERVICE.get(svc, svc.split(".")[-1].replace("_", " ").title() if svc else "Action")
                        lines.append(f"  • {svc_trad} {eid_short}")
                    elif "choose" in a:
                        for choix in a["choose"]:
                            conds = choix.get("conditions", [])
                            cond_txt = ""
                            for c in conds:
                                if c.get("id"):
                                    cond_txt = c["id"].replace("_", " ")
                            for seq in choix.get("sequence", []):
                                svc = seq.get("service", "")
                                target = seq.get("target", {})
                                eid_a = target.get("entity_id", "") if isinstance(target, dict) else ""
                                eid_short = eid_a.split(".")[-1].replace("_", " ").title() if eid_a else ""
                                svc_trad = TRAD_SERVICE.get(svc, svc.split(".")[-1].replace("_", " ").title() if svc else "Action")
                                if cond_txt:
                                    lines.append(f"  • If {cond_txt} → {svc_trad} {eid_short}")
                                else:
                                    lines.append(f"  • {svc_trad} {eid_short}")
                    elif "delay" in a:
                        lines.append(f"  • Wait {a['delay']}")
                    else:
                        lines.append(f"  • Custom action")
                    return lines

                msg += "\n⚡ Actions:\n"
                for a in auto_data.get("action", []):
                    for line in _format_action(a):
                        msg += line + "\n"

                if not auto_data.get("action"):
                    msg += "  • (no actions defined)\n"

                telegram_send_buttons(msg, [
                    {"text": "✅ Validate", "callback_data": "auto_confirm"},
                    {"text": "✏️ Modify", "callback_data": "auto_modify"},
                    {"text": "❌ Cancel", "callback_data": "auto_cancel"},
                ])
                add_history("assistant", msg)
                return ""
            except Exception as e:
                log.error(f"Automation pending: {e}")
                add_history("assistant", text_response)
        return text_response
    except Exception as e:
        log.error(f"❌ LLM API error: {e}")
        return "⚠️ The AI could not process this request. Please retry or check your provider configuration."

def transcrire_vocal(file_id):
    """Transcrit a message vocal Telegram via Google Speech API. CRASH-PROOF."""
    ogg_path = None
    wav_path = None
    try:
        import shutil
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            local_ffmpeg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "ffmpeg")
            if os.path.exists(local_ffmpeg):
                ffmpeg_bin = local_ffmpeg
            else:
                log.warning("Vocal: ffmpeg not found")
                return None
        import tempfile, subprocess
        url_info = f"https://api.telegram.org/bot{CFG['telegram_token']}/getFile?file_id={file_id}"
        r_info = requests.get(url_info, timeout=10)
        if r_info.status_code != 200:
            return None
        file_path = r_info.json().get("result", {}).get("file_path", "")
        if not file_path:
            return None
        url_dl = f"https://api.telegram.org/file/bot{CFG['telegram_token']}/{file_path}"
        r_dl = requests.get(url_dl, timeout=30)
        if r_dl.status_code != 200:
            return None
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f_ogg:
            f_ogg.write(r_dl.content)
            ogg_path = f_ogg.name
        flac_path = ogg_path.replace(".ogg", ".flac")
        wav_path = flac_path
        result = subprocess.run(
            [ffmpeg_bin, "-y", "-i", ogg_path, "-ar", "16000", "-ac", "1", "-f", "flac", flac_path],
            capture_output=True, timeout=30
        )
        if result.returncode != 0:
            log.error(f"Vocal: ffmpeg error")
            return None
        with open(flac_path, "rb") as f_audio:
            audio_data = f_audio.read()
        url_google = "http://www.google.com/speech-api/v2/recognize?output=json&lang=en-US&key=AIzaSyBOti4mM-6x9WDnZIjIeyEU21OpBXqWBgw"
        r_google = requests.post(url_google, data=audio_data, headers={"Content-Type": "audio/x-flac; rate=16000"}, timeout=15)
        for line in r_google.text.strip().split("\n"):
            line = line.strip()
            if not line or line == '{"result":[]}':
                continue
            try:
                data = json.loads(line)
                results_g = data.get("result", [])
                if results_g:
                    alternatives = results_g[0].get("alternative", [])
                    if alternatives:
                        text = alternatives[0].get("transcript", "")
                        if text:
                            log.info(f"🎤 Vocal: {text}")
                            return text
            except json.JSONDecodeError:
                continue
        log.warning("Vocal: Google n'a not reconnu of text")
        return None
    except Exception as e:
        log.error(f"Vocal: {e}")
        return None
    finally:
        for f in [ogg_path, wav_path]:
            if f:
                try:
                    os.remove(f)
                except Exception:
                    pass
