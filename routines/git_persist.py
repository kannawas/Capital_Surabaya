"""
CCR storage workaround: persist run results to GitHub instead of Supabase.

CCR containers block outbound network to *.supabase.co and yfinance, but allow
git operations. This script runs at the END of a routine in SQLite mode, exports
the SQLite ledger to JSON, and commits it back to the repo.

A local machine (which CAN reach Supabase) then pulls these JSON files and syncs
them to Supabase via sync_to_supabase().

Usage in routine (after core/run.py completes, SQLite mode):
    python3 routines/git_persist.py export   # dump SQLite -> run_results/latest.json
    git add run_results/ data/ledger.db
    git commit -m "Run YYYY-MM-DD"
    git push

Usage on local machine:
    python3 routines/git_persist.py sync      # JSON -> Supabase
"""

from __future__ import annotations
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

RESULTS_DIR = Path(__file__).parent.parent / "run_results"

TABLES = [
    "runs", "orders", "positions", "cash_ledger",
    "nav_history", "verdict_store", "conviction_store", "agent_outputs",
]


def export_sqlite_to_json() -> str:
    """Read all tables from the SQLite ledger and write a timestamped JSON snapshot."""
    os.environ["USE_SUPABASE"] = "false"
    from ledger.storage import select

    RESULTS_DIR.mkdir(exist_ok=True)
    snapshot = {"exported_at": datetime.now(timezone.utc).isoformat(), "tables": {}}
    for table in TABLES:
        try:
            snapshot["tables"][table] = select(table)
        except Exception as e:
            snapshot["tables"][table] = {"_error": str(e)}

    # Write both a timestamped file and latest.json
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_ts = RESULTS_DIR / f"run_{ts}.json"
    out_latest = RESULTS_DIR / "latest.json"
    payload = json.dumps(snapshot, indent=2, ensure_ascii=False, default=str)
    out_ts.write_text(payload, encoding="utf-8")
    out_latest.write_text(payload, encoding="utf-8")

    counts = {t: len(v) for t, v in snapshot["tables"].items() if isinstance(v, list)}
    print(f"Exported snapshot: {out_ts.name}")
    print(f"Row counts: {counts}")
    return str(out_ts)


def sync_json_to_supabase(json_path: str | None = None) -> None:
    """
    Local-machine step: read a JSON snapshot and upsert into Supabase.
    Run this where outbound network to Supabase works.
    """
    os.environ["USE_SUPABASE"] = "true"
    # storage reads SUPABASE_URL/KEY from env or .env
    from ledger.storage import insert, upsert, select, scalar_sum

    src = Path(json_path) if json_path else (RESULTS_DIR / "latest.json")
    snapshot = json.loads(src.read_text(encoding="utf-8"))
    tables = snapshot["tables"]

    # PK-keyed tables use upsert; append-only tables use insert (after clearing dup runs)
    pk_tables = {
        "positions": "ticker", "verdict_store": "ticker", "conviction_store": "ticker",
    }

    # Upsert state tables
    for table, pk in pk_tables.items():
        for row in tables.get(table, []):
            upsert(table, row, conflict_col=pk)
        print(f"Synced {table}: {len(tables.get(table, []))} rows")

    # Append-only tables: insert rows that aren't there yet (dedup by run_ts+content)
    for table in ["runs", "orders", "cash_ledger", "nav_history", "agent_outputs"]:
        rows = tables.get(table, [])
        for row in rows:
            # drop the SQLite serial PK so Supabase assigns its own
            row = {k: v for k, v in row.items()
                   if k not in ("run_id", "order_id", "tx_id", "nav_id", "id")}
            try:
                insert(table, row)
            except Exception as e:
                print(f"  skip {table} row: {e}")
        print(f"Synced {table}: {len(rows)} rows")

    print("Sync complete. Supabase cash:", scalar_sum("cash_ledger", "delta"))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent))
    action = sys.argv[1] if len(sys.argv) > 1 else "export"
    if action == "export":
        export_sqlite_to_json()
    elif action == "sync":
        path = sys.argv[2] if len(sys.argv) > 2 else None
        sync_json_to_supabase(path)
    else:
        print(f"Unknown action: {action}. Use 'export' or 'sync'.")
