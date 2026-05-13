#!/bin/bash
LOG=/tmp/home-assistant-companion-e2e.log
exec > "$LOG" 2>&1
set -u
echo "════════ E2E INSTALL TEST $(date -Iseconds) ════════"
echo ""

rm -rf /tmp/e2e-test-install

echo "═══ [1] git clone ═══"
cd /tmp
git clone https://github.com/MrMortalMonkey/home-assistant-companion.git e2e-test-install 2>&1 | tail -3
if [ ! -d /tmp/e2e-test-install ]; then echo "❌ clone failed"; exit 1; fi
cd /tmp/e2e-test-install
N=$(find . -maxdepth 2 -type f | grep -v '.git/' | wc -l)
echo "✅ Clone OK — $N files"
echo ""

echo "═══ [2] Structure verification ═══"
for f in README.md requirements.txt env.example install.sh Dockerfile \
         docker-compose.yml LICENSE .gitignore assistant.service.template \
         scripts/install_systemd.sh scripts/enable_beta_channel.sh \
         scripts/disable_beta_channel.sh \
         docs/INSTALL.md docs/CONFIGURATION.md docs/TROUBLESHOOTING.md docs/BETA_CHANNEL.md \
         addon/Dockerfile addon/config.yaml addon/run.sh; do
    if [ -f "$f" ]; then
        printf "  ✅ %-45s %6d b\n" "$f" "$(wc -c < "$f")"
    else
        printf "  ❌ %-45s MISSING\n" "$f"
    fi
done
echo ""

echo "═══ [3] Security: sensitive files absent ═══"
for f in config.json config.json.bak memory.db assistant.log deploy.log \
         addon/Dockerfile.txt assistant.service.txt; do
    if [ -f "$f" ]; then
        printf "  ❌ %-45s PROBLEM: present\n" "$f"
    else
        printf "  ✅ %-45s missing\n" "$f"
    fi
done
echo ""

echo "═══ [4] Scripts executables ═══"
for s in install.sh scripts/install_systemd.sh scripts/enable_beta_channel.sh \
         scripts/disable_beta_channel.sh addon/run.sh; do
    if [ -x "$s" ]; then
        printf "  ✅ %-45s chmod=%s\n" "$s" "$(stat -c '%a' "$s")"
    else
        printf "  ⚠️  %-45s chmod=%s (not executable)\n" "$s" "$(stat -c '%a' "$s")"
    fi
done
echo ""

echo "═══ [5] Test install.sh --from-env ═══"
cat > .env <<'ENVEOF'
TELEGRAM_TOKEN=111111111:AAABBBCCCDDDEEEFFFGGGHHHIIIJJJKKK_test
HA_URL=http://192.168.1.99:8123
HA_TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.fake_test_payload.signature_mock
ANTHROPIC_API_KEY=sk-ant-api03-test-fake-key-for-install-test-only-do-not-use
LLM_MONTHLY_BUDGET_USD=0
SMS_METHOD=ha_notify
ENVEOF

bash install.sh --no-interactive --from-env 2>&1 | tail -35
echo ""

echo "═══ [6] config.json verification ═══"
if [ -f config.json ]; then
    BYTES=$(wc -c < config.json)
    PERMS=$(stat -c '%a' config.json)
    echo "  ✅ config.json generated: $BYTES bytes, permissions $PERMS (expected: 600)"
    echo ""
    echo "  Keys and types :"
    python3 -c "
import json
with open('config.json') as f: cfg = json.load(f)
for k in sorted(cfg.keys()):
    v = cfg[k]
    if isinstance(v, str):
        print(f'    {k:30s} str  len={len(v)}')
    else:
        print(f'    {k:30s} {type(v).__name__:5s} = {v}')
"
    echo ""
    echo "  Semantic checks:"
    python3 -c "
import json, sys
with open('config.json') as f: cfg = json.load(f)
checks = [
    ('telegram_token injected',    cfg.get('telegram_token','').startswith('111111111:')),
    ('ha_url injected',            cfg.get('ha_url','') == 'http://192.168.1.99:8123'),
    ('ha_token injected',          cfg.get('ha_token','').startswith('eyJhbGciOi')),
    ('anthropic_api_key injected', cfg.get('anthropic_api_key','').startswith('sk-ant-api03-test')),
    ('sms_method ha_notify',       cfg.get('sms_method','') == 'ha_notify'),
    ('poll_interval_sec default',  cfg.get('poll_interval_sec') == 2),
    ('audit_interval_sec default', cfg.get('audit_interval_sec') == 1800),
    ('budget int',                 isinstance(cfg.get('llm_monthly_budget_usd'), int)),
    ('budget disabled',            cfg.get('llm_monthly_budget_usd') == 0),
    ('deploy_secret generated',    len(cfg.get('deploy_secret','')) == 64),
    ('telegram_chat_id empty',      cfg.get('telegram_chat_id','') == ''),
    ('SMS fields empty',           cfg.get('free_mobile_user','') == '' and cfg.get('smtp_user','') == ''),
]
failed = 0
for name, ok in checks:
    print(f'    {\"✅\" if ok else \"❌\"} {name}')
    if not ok: failed += 1
if failed: print(f'\\n  ❌ {failed}/{len(checks)} checks failed')
else:      print(f'\\n  ✅ {len(checks)}/{len(checks)} checks OK')
"
else
    echo "  ❌ config.json was not generated"
fi
echo ""

echo "═══ [7] Script syntax ═══"
for s in install.sh scripts/install_systemd.sh scripts/enable_beta_channel.sh \
         scripts/disable_beta_channel.sh addon/run.sh; do
    ERR=$(bash -n "$s" 2>&1)
    if [ -z "$ERR" ]; then
        printf "  ✅ %-45s OK\n" "$s"
    else
        printf "  ❌ %-45s ERROR\n" "$s"
        echo "$ERR" | head -3 | sed 's/^/      /'
    fi
done
echo ""

cd /tmp && rm -rf e2e-test-install
echo "  ✅ Test finished"
echo ""
echo "════════ FIN E2E TEST $(date -Iseconds) ════════"
