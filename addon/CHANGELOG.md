# Changelog

## 0.1.17 beta (2026-05-16)
- Added centralized Home Assistant config-write execution helper to enforce confirmation-gated writes consistently
- Improved entity matching with Home Assistant entity/device registry context (friendly names, device names, manufacturer/model, aliases)
- Added deterministic history-based fact helpers for "opened today" and "energy used today" responses
- Simplified user-facing fact replies to reduce noisy diagnostic output in Telegram
- Fixed self-healing error pipeline runtime issues (`_errors_buffer.clear`, signature tracking scope, retry argument)
- Removed remaining French room and messaging remnants in root runtime code paths

## 0.1.16 beta (2026-05-16)
- Switched Assist exposure detection to the Home Assistant WebSocket API command `homeassistant/expose_entity/list`
- Discovery now uses WebSocket exposure data for `conversation` when explicit flags are available
- Added `websocket-client` dependency for App runtime
- Kept registry-based exposure detection as fallback if WebSocket is unavailable

## 0.1.15 beta (2026-05-16)
- Removed per-entity Telegram approval prompts during discovery
- Discovery now auto-categorizes new entities silently instead of asking "Is this correct?"
- Added Assist exposure-aware discovery: when Assist exposure metadata is available, only entities exposed to Assist are auto-processed
- Existing unanswered discovery prompts are auto-closed to stop repeated notification spam

## 0.1.14 beta (2026-05-15)
- User-facing replies now prefer friendly device names (and room context) instead of raw entity IDs
- Native area/offline/energy responses were updated to display readable names
- Runtime action confirmations now report friendly names for single and multi-entity actions
- Outbound Telegram messages now auto-humanize known entity IDs to friendly names
- Prompt guidance reinforced to keep entity IDs for internal/tool use unless explicitly requested by the user

## 0.1.13 beta (2026-05-15)
- Fixed Home Assistant area cache usage in discovery/context paths to improve room/entity scope accuracy
- Added missing `skill_window_solar` learner to stop recurring monitoring runtime errors
- Expanded context building to include broader domain/entity coverage (lights, scenes, scripts, automations, sensors, etc.)
- Added native offline-entity reporting (including Zigbee-focused offline queries)
- Improved HA search tool output with domain inventory counts for better model-side discovery

## 0.1.12 beta (2026-05-14)
- Runtime control actions now execute immediately (no extra confirm step for on/off type actions)
- Home Assistant configuration changes remain confirmation-gated
- Added read-only `ha_get_history` tool for factual historical answers from Home Assistant
- Enhanced entity search tool with domain/area/limit filters for better data visibility

## 0.1.11 beta (2026-05-14)
- Reduced log noise: calendar events are no longer injected into every chat turn
- Calendar context is now fetched only for calendar/schedule/event-style questions
- Updated release metadata and docs to version 0.1.11

## 0.1.0 beta (2026-05-12)
- First HA App release
- Natural-language appliance and power-consumer setup through chat
- Sniper mode: adaptive polling 20s/60s
- Appliance program recognition
- Business model: savings table, real-time ROI
- Smart grace period by appliance type
- Immediate reminders (warm laundry, door unlock, dishes ready)
