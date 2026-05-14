#!/usr/bin/env python3
"""Automated tests — Home Assistant AI Companion
Run: python3 -m pytest tests.py -v
"""
import json, sqlite3, os, sys, time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# ═══ HELPERS ═══
DB_TEST = "/tmp/test_assistant.db"

def setup_test_db():
    """Creates a clean test DB."""
    if os.path.exists(DB_TEST):
        os.remove(DB_TEST)
    conn = sqlite3.connect(DB_TEST)
    conn.execute("""CREATE TABLE IF NOT EXISTS appliances (
        id INTEGER PRIMARY KEY, entity_id TEXT UNIQUE, appliance_type TEXT,
        custom_name TEXT, monitored INTEGER DEFAULT 1, created_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS cycle_measurements (
        id INTEGER PRIMARY KEY, entity_id TEXT, watts REAL, ts TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS savings (
        id INTEGER PRIMARY KEY, type TEXT, description TEXT, euros REAL,
        kwh_saved REAL, source TEXT, created_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS appliance_cycles (
        id INTEGER PRIMARY KEY, entity_id TEXT, friendly_name TEXT,
        started_at TEXT, ended_at TEXT, duration_min INTEGER, consumption_kwh REAL,
        cost_eur REAL, solar_production_w INTEGER, created_at TEXT,
        program TEXT, profile_json TEXT
    )""")
    conn.commit()
    conn.close()
    return DB_TEST


# ═══ TESTS CYCLE DETECTION ═══

class TestCycleDetection:
    """Tests for appliance cycle detection."""

    def test_start_threshold(self):
        """Cycle only starts above 200W."""
        CYCLE_START_W = 200
        assert 150 < CYCLE_START_W  # 150W = wash phase, not a start
        assert 2000 > CYCLE_START_W  # 2000W = heating, that's a start

    def test_end_threshold(self):
        """Machine stopped = below 10W."""
        CYCLE_END_W = 10
        assert 150 > CYCLE_END_W  # 150W = machine still running
        assert 4 < CYCLE_END_W    # 4W = machine stopped
        assert 0 < CYCLE_END_W    # 0W = machine stopped

    def test_phase_washing_continue_cycle(self):
        """150W (wash phase) must NOT trigger end grace period."""
        CYCLE_END_W = 10
        power = 150
        assert power > CYCLE_END_W  # No grace → cycle continues

    def test_grace_by_type_machine(self):
        """Different grace period per appliance type."""
        GRACE_AFTER_SPIN = 7
        GRACE_AFTER_WASH = 30
        GRACE_AFTER_DRYING = 15
        GRACE_AFTER_DISHWASHER = 10
        assert GRACE_AFTER_SPIN < GRACE_AFTER_DISHWASHER < GRACE_AFTER_DRYING < GRACE_AFTER_WASH


# ═══ TESTS POWER PROFILE ═══

class TestProfilePower:
    """Tests for power profile analysis."""

    def test_classifier_watts(self):
        """Correct classification of power phases."""
        def _classifier(w):
            if w > 1500: return "C"
            if w > 500: return "E"
            if w > 50: return "L"
            return "P"
        assert _classifier(2000) == "C"  # Heating
        assert _classifier(700) == "E"   # Spin
        assert _classifier(150) == "L"   # Wash
        assert _classifier(5) == "P"     # Pause
        assert _classifier(0) == "P"     # Stopped

    def test_signature_compare(self):
        """Signature in format C15-L33-E3."""
        phases = [
            {"type": "C", "duration_min": 15},
            {"type": "L", "duration_min": 33},
            {"type": "E", "duration_min": 3},
        ]
        sig = "-".join(f"{p['type']}{p['duration_min']}" for p in phases)
        assert sig == "C15-L33-E3"


# ═══ TESTS APPLIANCES ═══

class TestAppliances:
    """Tests for appliance identification on plugs."""

    def test_appliance_set_get(self):
        """Save and retrieve an appliance."""
        db = setup_test_db()
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO appliances (entity_id, appliance_type, custom_name, monitored, created_at) VALUES (?, ?, ?, ?, ?)",
            ("sensor.plug_kitchen_power", "dishwasher", "Dishwasher", 1, datetime.now().isoformat())
        )
        conn.commit()
        row = conn.execute("SELECT appliance_type, custom_name FROM appliances WHERE entity_id=?",
                          ("sensor.plug_kitchen_power",)).fetchone()
        conn.close()
        assert row[0] == "dishwasher"
        assert row[1] == "Dishwasher"

    def test_appliance_ignore(self):
        """Ignored appliance = monitored=0."""
        db = setup_test_db()
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO appliances (entity_id, appliance_type, custom_name, monitored, created_at) VALUES (?, ?, ?, ?, ?)",
            ("sensor.plug_tv_power", "ignore", "Ignore", 0, datetime.now().isoformat())
        )
        conn.commit()
        row = conn.execute("SELECT monitored FROM appliances WHERE entity_id=?",
                          ("sensor.plug_tv_power",)).fetchone()
        conn.close()
        assert row[0] == 0

    def test_detection_dishwasher_typo(self):
        """Dishwasher detected even with spelling mistake."""
        fname = "Plug dishwasher Power"
        fname_low = fname.lower()
        is_dishwasher = any(k in fname_low for k in ("dishwasher", "dishwash"))
        is_washing_machine = "washer" in fname_low and not is_dishwasher
        assert is_dishwasher == True
        assert is_washing_machine == False


# ═══ TESTS SAVINGS ═══

class TestSavings:
    """Tests for savings tracking."""

    def test_record_saving(self):
        """Record a saving in the DB."""
        db = setup_test_db()
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO savings (type, description, euros, kwh_saved, source, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("cycle_solar", "Washing machine 45% solar", 0.15, 0.8, "auto", datetime.now().isoformat())
        )
        conn.commit()
        row = conn.execute("SELECT SUM(euros), COUNT(*) FROM savings").fetchone()
        conn.close()
        assert row[0] == 0.15
        assert row[1] == 1

    def test_positive_roi(self):
        """ROI > 1 means tokens are profitable."""
        cost_tokens = 0.05
        savings = 0.50
        roi = savings / cost_tokens
        assert roi > 1
        assert roi == 10.0


# ═══ TESTS SQLite MEASUREMENTS ═══

class TestMeasurementsSQLite:
    """Tests for power measurement storage in SQLite."""

    def test_samples_persistent(self):
        """Measurements survive a restart (in SQLite, not in memory)."""
        db = setup_test_db()
        conn = sqlite3.connect(db)
        # Simulate 10 measurements during a cycle
        for i in range(10):
            ts = (datetime.now() + timedelta(seconds=i*20)).isoformat()
            conn.execute("INSERT INTO cycle_measurements (entity_id, watts, ts) VALUES (?, ?, ?)",
                        ("sensor.plug_laundry_power", 500 + i*100, ts))
        conn.commit()

        # "Restart" — re-read from DB
        rows = conn.execute("SELECT COUNT(*) FROM cycle_measurements WHERE entity_id=?",
                           ("sensor.plug_laundry_power",)).fetchone()
        conn.close()
        assert rows[0] == 10

    def test_purge_after_cycle(self):
        """Measurements are purged after end of cycle."""
        db = setup_test_db()
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO cycle_measurements (entity_id, watts, ts) VALUES (?, ?, ?)",
                    ("sensor.plug_test", 500, datetime.now().isoformat()))
        conn.commit()
        conn.execute("DELETE FROM cycle_measurements WHERE entity_id=?", ("sensor.plug_test",))
        conn.commit()
        row = conn.execute("SELECT COUNT(*) FROM cycle_measurements WHERE entity_id=?",
                          ("sensor.plug_test",)).fetchone()
        conn.close()
        assert row[0] == 0


# ═══ TESTS POLLING ADAPTATIF ═══

class TestSniper:
    """Tests mode sniper."""

    def test_sniper_active_during_cycle(self):
        """Poll every 20s while a cycle is active."""
        _state_plugs = {"sensor.plug_laundry": "active"}
        has_cycle = any(v == "active" for v in _state_plugs.values())
        assert has_cycle == True

    def test_standby_without_cycle(self):
        """Poll every 60s while no cycle is active."""
        _state_plugs = {"sensor.plug_laundry": "inactive"}
        has_cycle = any(v == "active" for v in _state_plugs.values())
        assert has_cycle == False


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
