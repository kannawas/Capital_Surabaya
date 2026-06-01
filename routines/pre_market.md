# Capital Surabaya — Pre-Market Pipeline
# Runs at 15:00 ICT (08:00 UTC) before US market open

You are the Capital Surabaya pipeline orchestrator running the pre-market cycle.

The post-close run (04:00 ICT) already produced intents and verdicts.
This run updates prices, re-checks macro/news for any overnight developments,
then re-runs the deterministic core with fresh prices before market open.

## Environment setup

```bash
if [ -f requirements.txt ]; then REPO=$(pwd); \
elif [ -f /repo/requirements.txt ]; then REPO=/repo; \
else REPO=$(dirname $(find / -name requirements.txt -path '*Capital*' 2>/dev/null | head -1)); fi
cd "$REPO"
echo "Repo root: $REPO"
ls requirements.txt || echo "WARNING: requirements.txt not found"

pip3 install -r requirements.txt -q || pip install -r requirements.txt -q || python3 -m pip install -r requirements.txt -q

export USE_SUPABASE=true
export SUPABASE_URL="https://bjiynevanmgopegmwcbi.supabase.co"
export SUPABASE_KEY="FILL_IN_SUPABASE_KEY"
export CUTOFF_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "Cutoff: $CUTOFF_TS"
```

**Important:** run every Python command from `$REPO`. Use `python3` (not `python`).

## Step 1 — Fetch fresh pre-market data

```bash
python3 -c "
from data.watchlist import sync_from_file, get_active
from data.packets import build_packet
import json, os

cutoff = os.environ['CUTOFF_TS']
sync_from_file()
tickers = get_active()

packets = {
    'macro_intelligence': build_packet('macro_intelligence', cutoff, tickers),
    'news_reporter':      build_packet('news_reporter',      cutoff, tickers),
}
with open('/tmp/packets_pm.json', 'w') as f:
    json.dump(packets, f, ensure_ascii=False)
print('Pre-market packets built.')
"
```

## Step 2 — Quick macro and news check (abbreviated)

Read the packets. Act as Macro Intelligence and News Reporter agents (abbreviated — focus only on material changes since the post-close run).

- Read prompts from `prompts/macro_intelligence_agent.md` and `prompts/news_reporter_agent.md`
- Use WebFetch/WebSearch for overnight developments (Fed speeches, pre-market news)
- If regime or event_lock changed since post-close: save updated values to `/tmp/out_macro_pm.json`
- If new tickers are news-flagged: save to `/tmp/out_news_pm.json`
- If nothing material changed: save `{"regime": "unchanged", "event_lock": false}` and `{"news_flagged": []}`

## Step 3 — Load post-close intents from Supabase

```bash
python3 -c "
from ledger.storage import select
import json

# Load the last execution output
rows = select('agent_outputs', where={'agent_name': 'execution'})
rows.sort(key=lambda r: r.get('run_ts',''), reverse=True)
if not rows:
    print('No execution output found — aborting')
    exit(1)

exec_out = json.loads(rows[0]['output_json'])
with open('/tmp/out_execution_pm.json', 'w') as f:
    json.dump(exec_out, f, ensure_ascii=False)
print(f'Loaded intents: {len(exec_out.get(\"intents\", []))} tickers')
"
```

## Step 4 — Run deterministic core with fresh prices

```bash
python3 -c "
import json, os
from ledger.storage import init_schema
from core.run import run

init_schema()

with open('/tmp/out_execution_pm.json') as f:
    exec_out = json.load(f)
with open('/tmp/out_macro_pm.json') as f:
    macro_pm = json.load(f)
with open('/tmp/out_news_pm.json') as f:
    news_pm = json.load(f)

# Use unchanged macro if no update
macro_override = None if macro_pm.get('regime') == 'unchanged' else macro_pm
news_flagged = news_pm.get('news_flagged', [])

report = run(
    intents=exec_out.get('intents', []),
    cutoff_ts=os.environ['CUTOFF_TS'],
    news_flagged=news_flagged,
    macro_override=macro_override,
    dry_run=False,
)
print('Fills:', [f for f in report.get('fills', []) if f.get('status') == 'filled'])
print(json.dumps({'nav_before': report.get('nav_before'), 'nav_after': report.get('nav_after')}, indent=2))
"
```

## Done

Pre-market pipeline complete. Orders posted to ledger. US market opens in ~30 minutes.
