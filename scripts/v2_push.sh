#!/bin/bash
set -u
LOG=/home/lolufe/assistant/scripts/e2e_test.log
exec > "$LOG" 2>&1
echo "════════ DOC ended_at daynee 29/04 $(date -Iseconds) ════════"
cd /home/lolufe/assistant

crontab -l > /tmp/crontab.backup 2>/dev/null || true
crontab -l 2>/dev/null | grep -v 'git_sync.sh' | crontab - 2>/dev/null || true

git add LESSONS.md

if git diff --cached --quiet; then
    echo "(nothing a commit)"
else
    git commit -m "Doc: sensors Ecojoko HC/HP fantomes supprimes - cloture 29/04/2026

Confirmation that the entities sensor.ecojoko_consumption_hc_grid and
_hp_grid were of the entities orphaned (HA keeps the entities after
unchecking of the source integration).

Manual removal via Settings → Appliances and services → Entities
→ loop → Remove. Clean final state.

Remaining open work : patch AI Assistant for use the
statistics ha-linky (to do in a session dedicated a cold)." 2>&1 | tail -3
fi

echo ""
echo "--- git log ---"
git log --oneline -4

echo ""
echo "--- git push ---"
git push origin main 2>&1 | tail -5

crontab /tmp/crontab.backup 2>/dev/null || true

echo "════════ FIN $(date -Iseconds) ════════"
