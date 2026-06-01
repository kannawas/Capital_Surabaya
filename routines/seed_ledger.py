"""
Create a seeded SQLite ledger.db to commit to the repo.

CCR clones this repo and uses the committed ledger.db as its starting state
(cash, watchlist). Run locally, then commit data/ledger.db.

Usage:
    python3 routines/seed_ledger.py            # seed $30,000, no positions
    python3 routines/seed_ledger.py 50000      # custom starting cash
"""

from __future__ import annotations
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Always seed in SQLite mode (this is the file CCR will clone)
os.environ["USE_SUPABASE"] = "false"

from ledger.storage import init_schema, select, scalar_sum
from ledger.positions import seed_cash
from data.watchlist import sync_from_file


def seed(starting_cash: float = 30_000.0) -> None:
    init_schema()

    # Watchlist
    tickers = sync_from_file()
    print(f"Watchlist seeded: {len(tickers)} tickers")

    # Cash (only if empty)
    existing = scalar_sum("cash_ledger", "delta")
    if existing == 0:
        ts = datetime.now(timezone.utc).isoformat()
        seed_cash(starting_cash, reason="injection", ts=ts)
        print(f"Starting cash seeded: ${starting_cash:,.2f}")
    else:
        print(f"Cash already present: ${existing:,.2f} (skipped)")

    print(f"Positions: {len(select('positions'))}")
    print(f"Final cash: ${scalar_sum('cash_ledger','delta'):,.2f}")
    print("\nNow commit: git add data/ledger.db && git commit -m 'Seed ledger'")


if __name__ == "__main__":
    cash = float(sys.argv[1]) if len(sys.argv) > 1 else 30_000.0
    seed(cash)
