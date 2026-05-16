# Changelog

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
