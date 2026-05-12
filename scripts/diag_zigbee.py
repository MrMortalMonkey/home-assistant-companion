#!/usr/bin/env python3
"""Check rate memory state + what the bot sees now."""
import sqlite3
DB = '/home/lolufe/assistant/memory.db'
conn = sqlite3.connect(DB)

print("=== Memory: all rate-related HC entries ===")
rows = conn.execute(
    "SELECT key_name, value_text FROM memory_store WHERE key_name LIKE '%rate%' OR key_name LIKE '%hc%' OR key_name LIKE '%hp%' OR key_name LIKE '%off_peak_hour%'"
).fetchall()
if rows:
    for r in rows:
        print(f"  {r[0]} = {r[1][:200] if r[1] else None}")
else:
    print("  (no entries)")

# Also: current active rate
print("\n=== Active rate (config) ===")
rows = conn.execute("SELECT key_name, value_text FROM memory_store WHERE key_name = 'current_rate' OR key_name LIKE '%rate_config%'").fetchall()
for r in rows: print(f"  {r[0]} = {r[1][:300] if r[1] else None}")
conn.close()

# Check in HA whether the Linky off-peak/peak stat is present
import json, requests
with open('/home/lolufe/assistant/config.json') as f: cfg = json.load(f)
r = requests.get(f"{cfg['ha_url']}/api/states",
                 headers={"Authorization": f"Bearer {cfg['ha_token']}"},
                 timeout=15, verify=False)
states = r.json()

print("\n=== All entities related to Linky / linky / ha-linky ===")
matched = [s for s in states if 'linky' in s['entity_id'].lower()]
for s in matched:
    eid = s['entity_id']
    state = s['state']
    fname = s.get('attributes', {}).get('friendly_name', '')
    print(f"  {eid}: state={state} fname={fname}")

if not matched:
    print("  (no Linky entity visible — statistics may be invisible via /api/states)")

# Test off-peak auto-detection
print("\n=== Off-peak auto-detection test: searching entities matching bot keywords ===")
keywords = ["hchc", "hchp", "index_hc", "index_hp", "consumption_hc", "consumption_hp",
            "off_peak_hours_index", "peak_hours_index"]
for s in states:
    eid_low = s['entity_id'].lower()
    if any(k in eid_low for k in keywords):
        print(f"  ✅ {s['entity_id']}")
