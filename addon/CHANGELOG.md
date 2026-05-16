# Changelog

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
