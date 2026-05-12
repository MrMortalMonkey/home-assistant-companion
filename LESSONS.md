# Foundational Lessons - Known Installation Pitfalls

This file captures issues found during testing so future installations can avoid the same debugging work.

## Washing Machine

### Rinse Pause Mistaken For Cycle End
- **Symptom:** The cycle appears to finish after 30 minutes instead of 90 minutes.
- **Cause:** A 5-15 minute pause between washing and spinning drops power below `CYCLE_END_W`.
- **Fix:** Use a smart grace period based on the last phase: longer after heating, shorter after spinning.

### Door Locked After Completion
- **Symptom:** The notification says the cycle is finished, but the door cannot be opened.
- **Cause:** The door lock remains engaged for 3-5 minutes after the motor stops.
- **Fix:** Tell the user that the door will unlock in about 5 minutes.

## Dryer

### Anti-Crease Creates 300+ Minute Cycles
- **Symptom:** A cycle is reported as 311 minutes instead of about 40 minutes.
- **Cause:** Anti-crease mode rotates slowly at 10-50 W every 10 minutes for 2-4 hours and resets the grace period.
- **Fix:** After the warm-clothes reminder, consumption below 200 W no longer resets the grace period.

### Gentle Cycle Not Detected
- **Symptom:** The dryer runs in eco mode but no notification is sent.
- **Cause:** Maximum power stays around 50-180 W and never crosses `CYCLE_START_W` at 200 W.
- **Fix:** If consumption stays above `CYCLE_END_W` for 5 continuous minutes, start the cycle automatically.

## Dishwasher

### Passive Drying
- **Symptom:** The cycle-end notification arrives about 30 minutes too early or too late.
- **Cause:** A passive drying phase at 0 W lasts 10-30 minutes near the end, followed by residual consumption.
- **Fix:** Use a dishwasher-specific grace period, `GRACE_AFTER_DISHWASHER`.

## Solar And Anker

### ECU Reports 0 W Between Updates
- **Symptom:** Solar production alternates between a normal value and 0 W.
- **Cause:** The APSystems ECU reports 0 W between 5-minute updates.
- **Fix:** Ignore 0 W samples when the previous value was above 0 W and it is daytime.

### Anker Solarbank Is Not An Appliance Cycle
- **Symptom:** The Anker plug triggers a false appliance cycle.
- **Cause:** Battery charging and discharging changes plug power enough to look like a cycle.
- **Fix:** Exclude the Anker plug from appliance-cycle monitoring.

## Heat Pump And Heating

### Thermostat On/Off Is Normal
- **Symptom:** Repeated alerts say the heat pump switched on or off.
- **Cause:** The thermostat normally stops and restarts the compressor.
- **Fix:** Do not alert on normal `climate` modes such as `auto` or `heat`.

## Zigbee

### Weak LQI After Adding A Device
- **Symptom:** A new device has LQI around 30-40 and poor performance.
- **Cause:** The routing table has not optimized yet.
- **Fix:** Wait 24 hours, or force Rediscover Network in Zigbee2MQTT and change Wi-Fi channel if there is interference.

### Offline Device Not Detected - Critical Bug (2026-04-24)
- **Symptom:** An unplugged Zigbee outlet stayed offline for 6 hours 24 minutes without a Telegram alert.
- **Root causes:** `linkquality` was not exposed by this Home Assistant installation, stale rows existed in `zigbee_outages`, and the fallback alert ran only once per day with a 24-hour threshold.
- **Decision:** Roll back the emergency patches and redesign `_monitored_zigbee` so it does not depend on `linkquality`.
- **Preferred fix:** Use `last_changed` while `state == "unavailable"`, scope checks to mapped physical devices, use per-device cooldowns, purge resolved stale absences, and test with a deliberate unplug event before production.

## Sensor Heartbeat

### Learned Freshness Thresholds (2026-04-25)
- **Context:** Offline detection needed a reliable learned baseline for important energy sensors.
- **Scope:** Eight explicit energy sensors plus rate sensors handled separately.
- **Design:** Store median, P95, and P99 update gaps in `sensor_heartbeat`; observe silently for 7 days; alert when current gaps exceed learned thresholds; recompute weekly.
- **Reason:** A single 30-minute threshold would create false nighttime alerts because sensor update behavior varies widely.
- **Reset:** Delete rows from `sensor_heartbeat` via SQLite and restart the service.

## Rate And Metering

### Zen Weekend Plus Not Supported By little_monkey (2026-04-27)
- **Symptom:** Ecojoko off-peak and peak sensors stayed `unknown` for hours and triggered false heartbeat alerts.
- **Cause:** `little_monkey` did not support the hybrid Zen Weekend Plus rate.
- **Decision:** Disable the affected sensors in Home Assistant and stop heartbeat monitoring for them.
- **Future action:** Re-enable them only if the integration adds rate support.

### Independent Off-Peak Source Through ha-linky (2026-04-29)
- **Context:** The bot needed a reliable source for off-peak calculations after the Ecojoko rate issue.
- **Solution:** Use the `ha-linky` add-on with the official Enedis API through Consumption API.
- **Validated result:** One year of consumption history imported, costs calculated retroactively, daily sync scheduled, and Home Assistant Energy configured.
- **Open work:** Either expose `Linky consumption (costs)` through a Home Assistant template sensor or query recorder statistics directly from AI Companion.

## Contributing Lessons

Open a GitHub issue with:

1. The affected device.
2. The observed symptom.
3. The identified cause.
4. The applied or desired fix.
