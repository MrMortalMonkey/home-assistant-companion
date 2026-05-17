# =============================================================================
# CONFIG.PY — Variables and constants
# User-modifiable without risk of breaking the script.
# No logic here. Only values.
# =============================================================================

import os

# ═══ PATHS ═══
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH  = os.path.join(BASE_DIR, "config.json")
DB_PATH      = os.path.join(BASE_DIR, "memory.db")
LOG_PATH     = os.path.join(BASE_DIR, "assistant.log")
BEHAVIOR_PROMPT = os.path.join(BASE_DIR, "behavior.txt")

# ═══ VERSION ═══
MODE    = "PROD"
VERSION = "0.2.5"

# ═══ LLM PROVIDER CONFIGURATION ═══
# Supported providers:
#   "anthropic"  — Anthropic API (default)
#   "openai"     — OpenAI API
#   "openrouter" — OpenRouter API
#   "ollama"     — Ollama (local)
#   "lmstudio"   — LM Studio (local)
LLM_PROVIDER = "anthropic"  # Change to switch AI provider

# Default models per provider (overridable in config.json)
# These are fallbacks — config.json values take precedence
LLM_MODELS = {
    "anthropic":  {"default": "claude-haiku-4-5-20251001",  "strong": "claude-sonnet-4-6"},
    "openai":     {"default": "gpt-4o-mini",               "strong": "gpt-4o"},
    "openrouter": {"default": "anthropic/claude-3.5-haiku", "strong": "anthropic/claude-3.5-sonnet"},
    "ollama":     {"default": "llama3.2",                  "strong": "llama3.2"},
    "lmstudio":   {"default": "local-model",               "strong": "local-model"},
}

DEFAULT_RATES = {
    "base": {
        "type": "base",
        "provider": "Default",
        "name": "Default base rate",
        "price_kwh": 0.2516,
        "subscription_month": 0,
    }
}

# ═══ APPLIANCES — Detection thresholds ═══
CYCLE_START_W = 200   # Minimum watts to start a cycle
CYCLE_END_W   = 10    # Watts below this = machine stopped

# ═══ APPLIANCES — Smart grace period (minutes) ═══
GRACE_AFTER_SPIN  = 7    # After spin (>500W): door unlock 5 min + margin
GRACE_AFTER_WASH    = 30   # After wash: rinse→spin pause possible
GRACE_AFTER_DRYING   = 45   # After drying: covers preheating pause (measured 38 min)
GRACE_AFTER_DISHWASHER = 10   # After dishwasher: steam drying

# ═══ APPLIANCES — Minimum duration (minutes) ═══
MIN_DRYER_DURATION    = 30   # A drying cycle < 30 min = it's a pause
MIN_WASHER_DURATION     = 25   # An express wash = 30 min
MIN_DISHWASHER_DURATION = 20   # A short cycle = 25 min

# ═══ SCHEDULING ═══
MACHINE_DAYS = {5, 6, 2}  # weekday() — Saturday, Sunday, Wednesday

# ═══ POLLING — Sniper mode ═══
PLUG_POLL_IDLE  = 60   # Seconds at rest
PLUG_POLL_ACTIVE = 20   # Seconds when a cycle is running

# ═══ BRIEFING ═══
WORKDAY_BRIEFING_HOUR = 7    # Weekday briefing hour
WEEKEND_BRIEFING_HOUR = 10   # Weekend briefing hour
EVENING_SUMMARY_HOUR       = 21   # Daily summary hour
WEEKLY_SUMMARY_HOUR      = 20   # Sunday summary hour

# ═══ ZIGBEE ═══
LOW_LQI = 50   # Below this = weak signal

# ═══ APPLIANCE TYPES ═══
APPLIANCE_TYPES = {
    "washing_machine": "🧺 Washing machine",
    "dryer": "👕 Dryer",
    "dishwasher": "🍽️ Dishwasher",
    "freezer": "❄️ Freezer",
    "four": "🔥 Oven",
    "standby_killer": "🔇 Standby killer",
    "energy_monitor": "📊 Energy monitor",
    "heat_pump_water_heater": "🔥 Thermodynamic water heater",
    "ev_charger": "🔌 EV charging station",
    "towel_warmer": "🛁 Towel warmer",
    "pool_pump": "🏊 Pool pump",
    "water_heater": "♨️ Water heater",
    "air_conditioning": "❄️ Air conditioning",
    "heater": "🌡️ Electric heater",
    "other": "🔌 Other (name it)",
    "ignore": "⬜ Ignore",
}

# ═══ AUTO-DISCOVERED ROLES ═══
ROLE_DEFINITIONS = {
    "realtime_consumption":     ["sensor.*real.*power*", "sensor.*instant.*power*",
                                  "sensor.*current.*power*", "sensor.*active.*power*",
                                  "sensor.*grid.*power*", "sensor.*power.*grid*",
                                  "sensor.*ecojoko.*realtime*", "sensor.*realtime.*consumption*"],
    "consumption_day_kwh":      ["sensor.*consumption.*total.*kwh*", "sensor.*daily.*energy*",
                                  "sensor.*energy.*today*", "sensor.*today.*energy*",
                                  "sensor.*kwh.*today*", "sensor.*today.*kwh*",
                                  "sensor.*energy.*day*", "sensor.*day.*energy*"],
    "consumption_day_cost":     ["sensor.*daily.*cost*", "sensor.*day.*cost*",
                                  "sensor.*energy.*cost.*today*", "sensor.*cost.*today*"],
    "solar_production_w":       ["sensor.*ecu.*current.*power*", "sensor.*solar.*power*",
                                  "sensor.*pv.*power*", "sensor.*photovoltaic.*power*",
                                  "sensor.*solar.*watt*", "sensor.*panel.*power*"],
    "solar_production_kwh":     ["sensor.*ecu.*today.*energy*", "sensor.*solar.*energy.*today*",
                                  "sensor.*solar.*today.*kwh*", "sensor.*pv.*today*",
                                  "sensor.*solar.*production.*today*"],
    "solar_production_lifetime":["sensor.*ecu.*lifetime.*energy*", "sensor.*solar.*total*",
                                  "sensor.*solar.*lifetime*", "sensor.*pv.*lifetime*"],
    "inverters_total":          ["sensor.*ecu.*inverters", "sensor.*inverter.*count*",
                                  "sensor.*inverters.*total*"],
    "inverters_online":         ["sensor.*ecu.*inverters.*online*", "sensor.*inverters.*online*",
                                  "sensor.*online.*inverters*"],
    "battery_soc":              ["sensor.*battery.*soc*", "sensor.*state.*of.*charge*",
                                  "sensor.*battery.*percent*", "sensor.*battery.*level*"],
    "battery_soc_anker":        ["sensor.*solarbank.*state.*charge*", "sensor.*solarbank.*soc*"],
    "battery_prod_solar":       ["sensor.*solarbank.*power.*solar*"],
    "battery_output":           ["sensor.*solarbank.*output*", "sensor.*battery.*output*"],
    "battery_power":            ["sensor.*solarbank.*power*", "sensor.*battery.*power*"],
    "battery_mode":             ["sensor.*solarbank.*mode*", "sensor.*battery.*mode*"],
    "heat_pump_climate":        ["climate.*heat.*pump*", "climate.*heatpump*",
                                  "climate.*heat_pump*", "climate.*pompe.*chaleur*"],
    "heat_pump_outdoor_temperature": ["sensor.*outdoor.*temperature*", "sensor.*outdoor.*temp*",
                                       "sensor.*outside.*temperature*", "sensor.*ext.*temp*"],
    "heat_pump_setpoint":       ["number.*temperature.*setpoint*", "number.*setpoint.*temp*"],
    "weather_temperature":      ["sensor.*outdoor.*temperature*", "sensor.*outdoor.*temp*",
                                  "sensor.*outside.*temp*"],
    "weather_alert":            ["sensor.*weather.*alert*", "sensor.*storm.*alert*"],
    "weather_next_rain":        ["sensor.*next.*rain*", "sensor.*rain.*forecast*"],
    "weather_rain_chance":      ["sensor.*rain.*chance*", "sensor.*precipitation.*probability*"],
    "weather_snow_chance":      ["sensor.*snow.*chance*"],
    "weather_wind_speed":       ["sensor.*wind.*speed*"],
    "weather_wind_gust":        ["sensor.*wind.*gust*"],
}

# ═══ AUTO-HEALING ═══
AUTO_HEAL_THRESHOLD = 3   # Occurrences/1h before correction
AUTO_HEAL_COOLDOWN = 3600  # Seconds between 2 attempts for same error

# ═══ TELEGRAM FILTERS ═══
MAX_DAILY_MESSAGES = 50   # Daily anti-spam limit
ANTI_DUPLICATE_SEC = 300  # 5 min between identical messages
