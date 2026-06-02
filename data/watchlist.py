"""Watchlist management — sync watchlist.json → storage (SQLite or Supabase).

Two read paths, kept deliberately separate:
  - get_active()           -> flat list[str]   (sent to agents — MUST stay blind)
  - get_watchlist_detail() -> rich rows        (UI only — source, score, group)
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

from ledger.storage import select, upsert, init_schema, USE_SUPABASE

WATCHLIST_FILE = Path(__file__).parent.parent / "watchlist.json"


def _ensure_columns():
    """Add source/score columns to the watchlist table if missing (SQLite only).
    Supabase schema is managed via supabase_schema.sql."""
    if USE_SUPABASE:
        return
    from ledger.storage import get_conn
    with get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(watchlist)").fetchall()}
        if "source" not in cols:
            conn.execute("ALTER TABLE watchlist ADD COLUMN source TEXT NOT NULL DEFAULT 'user'")
        if "score" not in cols:
            conn.execute("ALTER TABLE watchlist ADD COLUMN score REAL")
        if "pinned" not in cols:
            conn.execute("ALTER TABLE watchlist ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")


def sync_from_file(path: Path | None = None) -> list[str]:
    """Load tickers from watchlist.json and upsert into storage. Returns active list."""
    src = path or WATCHLIST_FILE
    raw = json.loads(src.read_text(encoding="utf-8"))
    tickers = [t.upper().strip() for t in raw["tickers"]]
    ts = datetime.now(timezone.utc).isoformat()

    init_schema()
    _ensure_columns()
    for ticker in tickers:
        # Only set source=user on first insert; don't clobber an agent row's source
        upsert("watchlist",
               {"ticker": ticker, "active": 1, "added_ts": ts, "source": "user"},
               conflict_col="ticker")
    return tickers


def get_active() -> list[str]:
    """Flat ticker list sent to agents. Blind — no source, no score, no group."""
    rows = select("watchlist", where={"active": 1}, order_by="ticker ASC")
    return [r["ticker"] for r in rows]


def get_groups(path: Path | None = None) -> dict[str, str]:
    src = path or WATCHLIST_FILE
    raw = json.loads(src.read_text(encoding="utf-8"))
    return {t.upper(): g for t, g in raw.get("groups", {}).items()}


def get_watchlist_detail() -> dict:
    """
    Rich watchlist view for the UI ONLY. Never sent to agents.

    Returns:
        {
          "active":  [{ticker, source, score, group, pinned, added_ts, is_new}, ...],
          "removed": [{ticker, source, group, cooldown_until, note}, ...],
        }
    """
    _ensure_columns()
    groups = get_groups()
    today = datetime.now(timezone.utc).date().isoformat()

    rows = select("watchlist", order_by="ticker ASC")
    active, removed = [], []
    for r in rows:
        ticker = r["ticker"]
        added = (r.get("added_ts") or "")[:10]
        rec = {
            "ticker": ticker,
            "source": r.get("source", "user"),
            "score": r.get("score"),
            "group": groups.get(ticker, "—"),
            "pinned": bool(r.get("pinned", 0)),
            "added_ts": r.get("added_ts"),
            "is_new": (added == today),
        }
        if r.get("active", 1):
            active.append(rec)
        else:
            rec["cooldown_until"] = r.get("cooldown_until")
            rec["note"] = r.get("note")
            removed.append(rec)
    return {"active": active, "removed": removed}
