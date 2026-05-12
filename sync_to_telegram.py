#!/usr/bin/env python3
"""
STUB — sync_to_telegram.py
==========================
This file is a stub created to stop spam in sync.log
("No such file or directory") caused by an orphaned cron job.

ROOT CAUSE ACTION REQUIRED (via SSH):
    crontab -e
    → find the line that calls sync_to_telegram.py
    → delete it
    → then rm this file

Created on 2026-04-18 17:44:05
"""
import sys, os
# Log to track how many times the cron still runs
try:
    with open('/home/lolufe/assistant/sync.log', 'a') as f:
        from datetime import datetime
        f.write(f"{datetime.now().isoformat()}: stub sync_to_telegram.py called - clean up the cron\n")
except Exception:
    pass
sys.exit(0)
