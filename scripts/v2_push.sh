#!/bin/bash
set -u
LOG=/home/lolufe/assistant/scripts/e2e_test.log
exec > "$LOG" 2>&1
echo "════════ DOC update $(date -Iseconds) ════════"
cd /home/lolufe/assistant

crontab -l > /tmp/crontab.backup 2>/dev/null || true
crontab -l 2>/dev/null | grep -v 'git_sync.sh' | crontab - 2>/dev/null || true

git add LESSONS.md

if git diff --cached --quiet; then
    echo "(nothing to commit)"
else
    git commit -m "Doc: clean obsolete rate sensor notes

Confirmation that the entities sensor.ecojoko_consumption_hc_grid and
_hp_grid were orphaned entities. Home Assistant keeps entities after
the source integration is disabled.

Manual removal path: Settings → Devices and services → Entities
→ open the entity → Remove. Clean final state.

Remaining open work: use Home Assistant Energy statistics when available." 2>&1 | tail -3
fi

echo ""
echo "--- git log ---"
git log --oneline -4

echo ""
echo "--- git push ---"
git push origin main 2>&1 | tail -5

crontab /tmp/crontab.backup 2>/dev/null || true

echo "════════ DONE $(date -Iseconds) ════════"
