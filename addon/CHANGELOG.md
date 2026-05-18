# Changelog

## 0.2.8 beta (2026-05-18)
- Fixed intelligence cycle counter stuck at #0: `_cycle_intelligence` now reads `shared._intelligence_counter` directly instead of a stale local copy imported at startup — periodic tasks (hourly learning, daily analysis, etc.) now fire at correct intervals
- Fixed solar "0W in daylight" alert firing on installations with no solar panels: alert is now gated on `role_get("solar_production_w")` being configured
- Fixed deploy server health-check alert spamming on addon installs: `_monitoring_deploy_server` is now disabled by default and only runs when `enable_deploy_server: true` is set in config
- Fixed entity classification re-running 'ignore'-category entities on every intelligence cycle: `entity_map` lookup now includes all categories, not just non-ignore; classification is also capped at 20 new entities per cycle to prevent LLM storms on first boot
- Added role exclusion patterns to `ROLE_DEFINITIONS`: `realtime_consumption` now excludes UPS/generator sensors; `battery_soc` now excludes phone/tablet/laptop device batteries — prevents false-positive role assignments from auto-discovery
- Reduced offline entity audit log noise: "N entities offline" is only logged when the count changes, not every 30-minute audit cycle

## 0.2.7 beta (2026-05-18)
- Added persistent memory: agent stores facts about your home (device names, room assignments, preferences) across all future conversations via `ha_remember` tool; `/memory` command lists stored facts with per-fact delete buttons
- Added automation editing: `ha_update_automation` tool fetches current automation, shows a diff preview with Apply/Cancel before writing
- Added automation deletion: `ha_delete_automation` tool with confirmation prompt before permanent removal
- Added helper creation: `ha_create_helper` tool supports input_boolean, input_number, input_select, input_text, timer, and counter — building blocks for more complex automations
- Added script creation: `ha_create_script` tool creates reusable HA action sequences callable from automations, dashboards, or Assist voice
- Fixed `_send_proactive_recommendations` NameError in monitoring loop (private function was not included in explicit imports)
- Fixed SQLite database-locked errors in entity classification loop: separated LLM calls (Phase 1) from DB writes (Phase 2) to eliminate write-lock contention
- Fixed agent ignoring user-provided entity IDs: added explicit rule to use entity_id as-is and call ha_search_entities to retrieve state
- Added LLM API retry: failed API calls (timeout, 504) retry once after 4s before returning an error message
- Changed default log level from WARNING to INFO; added configurable `log_level` option (debug/info/warning/error) in addon UI and config.json
- Updated `/automations` command to show automation slug IDs for use with edit/delete requests

## 0.2.6 beta (2026-05-17)
- Added HA Assist integration: custom conversation component (`custom_components/companion_agent/`) registers as a native Assist backend — use the agent from the HA dashboard, Siri, Google, or Alexa without Telegram
- Added HTTP Conversation API (port 8502): `POST /conversation` forwards text to the AI engine and returns a response; `GET /health` returns version info; optional Bearer token auth via `conversation_secret`
- Added scene creation: LLM can now call `ha_create_scene` tool to capture multi-entity states; shows a preview with Validate/Cancel buttons before writing to HA
- Added proactive pattern detection: intelligence loop tracks entity state transitions by hour across days; when a 5-day pattern is detected with no matching automation, sends a Telegram suggestion with one-tap "Create automation" / "Ignore" buttons
- Fixed Zigbee device detection: ZHA WebSocket API is now queried first (authoritative), entity attribute scan runs in parallel for Zigbee2MQTT — both merged with deduplication; eliminates "0 devices" false reports
- Added `conversation_port` and `conversation_secret` options to addon configuration UI; exposed port 8502/tcp in addon manifest

## 0.2.5 beta (2026-05-17)
- Fixed "ALERTE INTELLIGENCE" → "INTELLIGENCE ALERT" in solar/anomaly Telegram alerts
- Fixed "ALERTE: HA unreachable" → "ALERT: HA unreachable" in internet outage SMS

## 0.2.4 beta (2026-05-17)
- Added `/status` command: comprehensive HA ecosystem health snapshot covering entities, integrations, automations, batteries, persistent notifications, updates, recent errors, and host metrics
- Added `/integrations` command: detailed view of all HA config entry states (loaded, retrying, failed, disabled)
- Enhanced `/automations` command: shows last-triggered timestamp for each automation sorted by recency
- Fixed Zigbee device detection: now handles ZHA (`lqi` attribute) and Zigbee2MQTT (`linkquality`) and falls back to ZHA WebSocket API when no devices found via states
- Background monitoring: `_monitor_ha_health()` now alerts on new HA integration failures and new persistent notifications via Telegram
- Added per-room power breakdown to AI context: LLM can now answer "how much power is my living room using?" with live watt data and estimated hourly cost
- Added today's energy per sensor to AI context: LLM uses HA statistics API to answer energy/cost questions by device or room
- Added `ha_get_statistics_today()`, `ha_get_config_entries()`, `ha_get_error_log_tail()`, `ha_get_plain()` helper functions to shared.py

## 0.2.3 beta (2026-05-17)
- Added missing `skill_health_host` function that collects RAM, disk, and HA latency metrics every 15 minutes — eliminates recurring NameError in intelligence loop
- Fixed SQLite "database is locked" errors under concurrent thread load: enabled WAL journal mode at DB init and added `timeout=10` to all connections across skills.py, shared.py, and assistant.py
- Fixed remaining French string in log output: "Question intelligente" → "Intelligent query"

## 0.2.2 beta (2026-05-16)
- Fixed remaining user-visible strings: "RESEAU ZIGBEE" → "ZIGBEE NETWORK", "Tors online" → "All online", "LQI CRITIQUE" → "LQI CRITICAL", "meiltheir" → "best", "DETAIL COMPLET" → "FULL DETAIL", "SOLAR ENTITIES SOLAR" → "SOLAR ENTITIES", "AUTOMATISATIONS" → "AUTOMATIONS", "Cartographie" → "Entity map", "Charge solar" → "Solar charge", "Injection home" → "Home output"
- Fixed weather temperature unit: now reads `temperature_unit` attribute from HA weather entity instead of always showing °C
- Added "No data — run /scan" hint in /energy CONSUMPTION section when no roles are assigned yet
- Fixed /energy daily cost to use configured `currency` symbol instead of hardcoded €
- Expanded ROLE_DEFINITIONS patterns to match a much broader range of HA entity naming conventions (generic power, energy, solar, battery patterns)
- Energy dashboard discovery now seeds the roles table directly so /energy shows kWh data without requiring /scan
- HA App: added feature toggles to configuration UI — enable/disable morning briefing, evening summary, and appliance cycle detection
- HA App: added regional settings to configuration UI — timezone, country_code, electricity_rate_kwh, currency
- HA App: removed "Enable Remote Server" (deploy server) option from configuration
- `/diag_carto` command renamed to `/diag_map` for consistency

## 0.2.1 beta (2026-05-16)
- Fixed missing `skill_optimisation_rate` function causing `monitoring_core intelligence` NameError every 5 minutes
- Fixed noisy 404 warnings on optional HA Energy dashboard endpoints (`config/energy`, `energy/info`) — now logged at DEBUG level when the Energy dashboard is not configured
- Added message logging before channel lock check so incoming Telegram messages always appear in logs

## 0.2.0 beta (2026-05-16)
- Fixed runtime crash: `plt.tight_layout()` typo corrected in energy graph generation
- Fixed morning briefing firing at 5am — now correctly uses `WORKDAY_BRIEFING_HOUR` (7am) / `WEEKEND_BRIEFING_HOUR` (10am) constants
- Fixed undefined variable crash in automation-modify flow; block moved to correct scope in `handle_message()`
- Fixed context graph cache bypass that caused cache to be skipped on every call
- Fixed tautological battery entity filter (duplicated OR/AND conditions simplified)
- Fixed battery monitoring loop: sleep extended to 3600s to prevent repeated 9am alerts
- Fixed duplicate `cmd_programs` definition (stale version using deprecated skill key removed)
- Removed dead `_enregistrer_program` function (never called)
- Removed duplicate keys in Telegram commands dispatch dict (`energy`, `automations`, `rooms`, `memory_store`, `export`)
- Fixed token cost calculation to be provider-aware (Anthropic, OpenAI, OpenRouter, Ollama, LM Studio)
- Translated all French user-visible strings, comments, function names, and variable names to English
- Renamed internal functions: `_calculer_signature_cycle` → `_calculate_cycle_signature`, `_notif_tempo_ejp` → `_notify_tempo_ejp`, `_rollback_si_errors_repetees` → `_rollback_on_repeated_errors`
- Removed hardcoded `Europe/Paris` timezone — configurable via `timezone` key in config.json
- Replaced hardcoded French public holiday list with configurable multi-country table (`country_code`: fr/us/gb/au/de/none)
- Replaced hardcoded `BASELINE_ENTITIES` (developer's personal entity IDs) with automatic discovery from HA Energy dashboard API, with config.json override support (`baseline_entities`)
- Added configurable electricity rate for non-French providers (`electricity_rate_kwh`, `currency` config keys)
- Added Tempo/EJP alert guard: disabled by default, opt-in via `enable_tempo_ejp: true` in config.json (France EDF-specific)
- Changed default `MODE` from `"DEV"` to `"PROD"` — eliminates security bypass for new installs
- Fixed `deploy_server.py` hardcoded `/home/lolufe/assistant` paths — now derived from `__file__` or `ASSISTANT_DIR` env var
- Added behavior.txt header noting the device list is an example to be replaced with user's own devices
- Updated README: corrected Google Home/Alexa capability description; clarified `/budget` cost estimate accuracy
- Documented new config keys: `timezone`, `country_code`, `electricity_rate_kwh`, `currency`, `enable_tempo_ejp`, `baseline_entities`
- All changes mirrored to `addon/app/` counterparts

## 0.1.19 beta (2026-05-16)
- Added missing `skill_heat_pump_behavior` learner used by the intelligence loop
- Fixed recurring runtime `NameError` in `monitoring_core intelligence` caused by the missing heat-pump skill function
- Added robust heat-pump/outdoor-temperature/consumption fallback detection to keep learning active even when some roles are not mapped yet

## 0.1.18 beta (2026-05-16)
- Fixed false leak alerts on startup by changing water leak detection to transition-based behavior
- Added first-seen state baselining for leak sensors so retained `on` states at boot no longer trigger immediate Telegram alerts
- Tightened leak entity classification to prioritize binary leak/problem classes and explicit leak/flood naming

## 0.1.17 beta (2026-05-16)
- Added centralized Home Assistant config-write execution helper to enforce confirmation-gated writes consistently
- Improved entity matching with Home Assistant entity/device registry context (friendly names, device names, manufacturer/model, aliases)
- Added deterministic history-based fact helpers for "opened today" and "energy used today" responses
- Simplified user-facing fact replies to reduce noisy diagnostic output in Telegram
- Fixed self-healing error pipeline runtime issues (`_errors_buffer.clear`, signature tracking scope, retry argument)
- Removed remaining French room and messaging remnants in root runtime code paths

> Historical beta entries were retired to match the single-active-version release policy.
