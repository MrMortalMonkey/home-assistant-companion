# Changelog

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
