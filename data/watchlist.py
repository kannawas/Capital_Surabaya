"""Watchlist management — sync watchlist.json → storage (SQLite or Supabase)."""

from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

from ledger.storage import select, upsert, init_schema

WATCHLIST_FILE = Path(__file__).parent.parent / "watchlist.json"


def sync_from_file(path: Path | None = None) -> list[str]:
    """Load tickers from watchlist.json and upsert into storage. Returns active list."""
    src = path or WATCHLIST_FILE
    raw = json.loads(src.read_text(encoding="utf-8"))
    tickers = [t.upper().strip() for t in raw["tickers"]]
    ts = datetime.now(timezone.utc).isoformat()

    init_schema()
    for ticker in tickers:
        upsert("watchlist", {"ticker": ticker, "active": 1, "added_ts": ts},
               conflict_col="ticker")
    return tickers


def get_active() -> list[str]:
    rows = select("watchlist", where={"active": 1}, order_by="ticker ASC")
    return [r["ticker"] for r in rows]


def get_groups(path: Path | None = None) -> dict[str, str]:
    src = path or WATCHLIST_FILE
    raw = json.loads(src.read_text(encoding="utf-8"))
    return {t.upper(): g for t, g in raw.get("groups", {}).items()}
