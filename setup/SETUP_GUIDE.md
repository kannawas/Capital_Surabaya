# Capital Surabaya — Setup Guide (Routines + Supabase)

## Prerequisites
- Claude Pro account (covers Routines usage)
- GitHub account (free)
- Supabase account (free tier, 500MB)

---

## Step 1: Create GitHub Repository

1. Go to github.com → New repository
2. Name: `capital-surabaya` (private recommended)
3. Push this project:
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/kannawas/Capital_Surabaya.git
git push -u origin main
```

---

## Step 2: Create Supabase Project

1. Go to supabase.com → New project
2. Note your:
   - **Project URL**: `https://xxxxxxxxxxxx.supabase.co`
   - **Anon key**: `eyJhbGci...` (Settings → API → anon public)
3. Open SQL Editor → New query
4. Paste and run: `setup/supabase_schema.sql`
5. Verify tables created: Database → Tables

---

## Step 3: Seed Initial Data

Run locally before first Routine run:
```bash
# Copy env
cp .env.example .env
# Fill in SUPABASE_URL and SUPABASE_KEY in .env
# Set USE_SUPABASE=true

python -c "
from ledger.storage import init_schema
from data.watchlist import sync_from_file
sync_from_file()
print('Watchlist synced to Supabase')
"

# Seed starting cash
python -c "
from ledger.storage import insert
from datetime import datetime, timezone
ts = datetime.now(timezone.utc).isoformat()
insert('cash_ledger', {'delta': 287500.0, 'reason': 'injection', 'ts': ts})
print('Starting cash seeded: \$287,500')
"
```

---

## Step 4: Create Routines in Claude Code

In Claude Code, tell it:

> "Create two Routines for Capital Surabaya paper trading:
> 1. post_close — runs at 21:00 UTC daily (04:00 ICT)
> 2. pre_market — runs at 08:00 UTC daily (15:00 ICT)
> GitHub repo: https://github.com/kannawas/Capital_Surabaya
> Use the prompts in routines/post_close.md and routines/pre_market.md"

Then fill in SUPABASE_URL and SUPABASE_KEY in the Routine prompts before saving.

---

## Step 5: Verify First Run

After the first Routine runs:
```bash
# Check Supabase via Python
python -c "
from ledger.storage import select
from ledger.nav import compute_nav
import os
os.environ['USE_SUPABASE'] = 'true'

nav = compute_nav(update_prices=True, save_snapshot=False)
print('NAV:', nav['nav'])
print('Positions:', nav['positions'])

runs = select('runs')
print('Runs completed:', len(runs))
"
```

---

## Monitoring

- Routines dashboard: https://claude.ai/code/routines
- Supabase tables: supabase.com/dashboard/project/bjiynevanmgopegmwcbi/editor
- Logs: check `agent_outputs` table for each agent's output per run
