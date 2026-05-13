# =============================================================================
# LOCALIZATION - Home Assistant AI Companion
# =============================================================================
# All user-facing strings in a single file.
# The project currently ships English user-facing text only.
# =============================================================================

LANG = {
    "en": {
        # Cycles
        "cycle_start": "🔄 {name} started",
        "cycle_power": "Power: {watts}W | {hour}",
        "cycle_solar_cover": "☀️ Solar coverage: {pct}%",
        "cycle_end": "✅ {name} done — {duration} min",
        "cycle_cost_solar_free": "☀️ {kwh} kWh — 100% solar, free!",
        "cycle_cost_solar_partial": "💰 {kwh} kWh — {cost}€ ({pct}% solar, saved {eco}€)",
        "cycle_cost_grid": "💰 {kwh} kWh — {cost}€",
        "cycle_month_savings": "📈 This month: {euros}€ saved",

        # Reminders
        "laundry_warm_reminder": "👕 {name} — Warm clothes!\nDrying done ({duration} min).\nTake out now — easier to fold.\n⏳ Anti-crease running, ~{grace} min left.",
        "reminder_washing_machine": "🧺 {name} — Cycle done!\nDuration: {duration} min.\n🚪 Door unlocks in ~5 min.\nPrepare the dryer or clothesline.",
        "reminder_dishwasher": "🍽️ {name} — Dishes ready!\nCycle done ({duration} min).\n⚠️ Caution: dishes are hot — wait 10 min.",
        "reminder_other": "✅ {name} — Cycle ending.\nDuration: {duration} min.",

        # Sniper
        "sniper_on": "🎯 Sniper mode — polling {sec}s",
        "sniper_off": "😴 Idle mode — polling {sec}s",

        # Appliances
        "appliance_question": "🔌 What appliance is on:\n**{name}**?",
        "appliance_restant": "({nb} plug(s) remaining)",
        "appliance_done": "✅ Appliance setup complete!\nThe script now knows what's on each plug.",
        "appliance_washing_machine": "🧺 Washing machine",
        "appliance_dryer": "👕 Dryer",
        "appliance_dishwasher": "🍽️ Dishwasher",
        "appliance_freezer": "❄️ Freezer",
        "appliance_other": "🔌 Other",
        "appliance_ignore": "⬜ Ignore",

        # Wizard
        "wizard_welcome": "🏠 WELCOME — Home Assistant AI Companion",
        "wizard_ha_url": "📡 STEP 1/4 — Home Assistant\nSend me your Home Assistant URL.",
        "wizard_ha_token": "📡 STEP 2/4 — Access token\nSend me your HA long-lived token (eyJ...)",
        "wizard_anthropic": "🧠 STEP 3/4 — AI provider credentials\nSend me the API key for your selected provider.",
        "wizard_sms": "🔐 STEP 4/4 — Security (unlock code)",
        "wizard_complete": "🎉 SETUP COMPLETE",

        # Errors
        "error_titre": "🔴 {nb} ERROR(S) DETECTED",
    }
}

def t(key, **kwargs):
    """Return the English string for a key."""
    texts = LANG["en"]
    template = texts.get(key, LANG["en"].get(key, key))
    try:
        return template.format(**kwargs)
    except Exception:
        return template
