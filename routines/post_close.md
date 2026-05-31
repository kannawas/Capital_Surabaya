# Capital Surabaya — Post-Close Pipeline
# Runs at 04:00 ICT (21:00 UTC) after US market close

You are the Capital Surabaya pipeline orchestrator running the post-close cycle.

## Environment setup

Run this first:
```bash
cd /repo
pip install -r requirements.txt -q
export USE_SUPABASE=true
export SUPABASE_URL="https://bjiynevanmgopegmwcbi.supabase.co"
export SUPABASE_KEY="FILL_IN_SUPABASE_KEY"
export CUTOFF_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "Cutoff: $CUTOFF_TS"
```

## Step 1 — Fetch market data

```bash
python -c "
from data.watchlist import sync_from_file, get_active, get_groups
from data.prices import fetch_ohlcv, compute_technicals
from data.macro import fetch_macro
from data.news import fetch_news
from data.packets import build_packet
import json, os

cutoff = os.environ['CUTOFF_TS']
sync_from_file()
tickers = get_active()
print('Tickers:', tickers)

packets = {
    'technical_screener': build_packet('technical_screener', cutoff, tickers),
    'macro_intelligence':  build_packet('macro_intelligence',  cutoff, tickers),
    'news_reporter':       build_packet('news_reporter',       cutoff, tickers),
    'fundamental_thesis':  build_packet('fundamental_thesis',  cutoff, tickers),
}
with open('/tmp/packets.json', 'w') as f:
    json.dump(packets, f, ensure_ascii=False)
print('Packets built for:', list(packets.keys()))
"
```

## Step 2 — Read packets and act as each research agent

Read the packets from `/tmp/packets.json`. Then, in sequence, act as each of the four research agents below. For each agent, read ONLY its designated packet section — do not cross-contaminate.

Read the full agent prompt from the repo file, then apply it to the packet data.

### 2a. Technical Screener Agent
- Read prompt: `/repo/prompts/technical_screener_agent.md`
- Data: `packets["technical_screener"]`
- Save output JSON to `/tmp/out_technical.json`

### 2b. Macro Intelligence Agent
- Read prompt: `/repo/prompts/macro_intelligence_agent.md`
- Data: `packets["macro_intelligence"]`
- Use WebFetch/WebSearch for current macro news (Fed, CPI, NFP)
- Save output JSON to `/tmp/out_macro.json`

### 2c. News Reporter Agent
- Read prompt: `/repo/prompts/news_reporter_agent.md`
- Data: `packets["news_reporter"]`
- Use WebFetch/WebSearch for ticker news
- Save output JSON to `/tmp/out_news.json`

### 2d. Fundamental Thesis Agent
- Read prompt: `/repo/prompts/fundamental_thesis_agent.md`
- Data: `packets["fundamental_thesis"]`
- Use WebFetch/WebSearch for SEC filings and financial data for triggered tickers only
- Save output JSON to `/tmp/out_thesis.json`

## Step 3 — Save thesis verdicts

```bash
python -c "
import json, os
from ledger.storage import init_schema
from core.state import save_thesis_verdict

init_schema()
with open('/tmp/out_thesis.json') as f:
    thesis = json.load(f)

valid = {'intact','weakening','watch','broken'}
cutoff = os.environ['CUTOFF_TS']
for v in thesis.get('verdicts', []):
    if v.get('verdict') in valid:
        save_thesis_verdict(v['ticker'], v['verdict'], run_ts=cutoff)
        print(f'Verdict: {v[\"ticker\"]} = {v[\"verdict\"]}')
"
```

## Step 4 — Act as Execution Agent

- Read prompt: `/repo/prompts/execution_agent.md`
- Data: combine outputs from steps 2a–2d as research_outputs (NO portfolio data)
- Produce intents JSON
- Save to `/tmp/out_execution.json`

## Step 5 — Run deterministic core

```bash
python -c "
import json, os
from ledger.storage import init_schema
from core.run import run

init_schema()
with open('/tmp/out_execution.json') as f:
    exec_out = json.load(f)
with open('/tmp/out_macro.json') as f:
    macro_out = json.load(f)
with open('/tmp/out_news.json') as f:
    news_out = json.load(f)

intents = exec_out.get('intents', [])
news_flagged = news_out.get('news_flagged', [])

report = run(
    intents=intents,
    cutoff_ts=os.environ['CUTOFF_TS'],
    news_flagged=news_flagged,
    macro_override=macro_out,
    dry_run=False,
)
print(json.dumps(report, indent=2, default=str))
"
```

## Step 6 — Save agent outputs for audit trail

```bash
python -c "
import json, os
from ledger.storage import insert
from datetime import datetime, timezone

now = datetime.now(timezone.utc).isoformat()
run_ts = os.environ['CUTOFF_TS']

for agent, fname in [
    ('technical_screener', '/tmp/out_technical.json'),
    ('macro_intelligence',  '/tmp/out_macro.json'),
    ('news_reporter',       '/tmp/out_news.json'),
    ('fundamental_thesis',  '/tmp/out_thesis.json'),
    ('execution',           '/tmp/out_execution.json'),
]:
    try:
        with open(fname) as f:
            data = json.load(f)
        insert('agent_outputs', {
            'run_ts': run_ts,
            'agent_name': agent,
            'output_json': json.dumps(data, ensure_ascii=False),
            'created_at': now,
        })
        print(f'Saved output: {agent}')
    except Exception as e:
        print(f'Warning: could not save {agent}: {e}')
"
```

## Done

Post-close pipeline complete. Results are in Supabase. The pre-market run at 15:00 ICT will use the same Execution intents to confirm fills.
