"""Watchlist management — sync watchlist.json -> SQLite."""

from __future__ import annotations
import json
from pathlib import Path

from ledger.db import get_conn, init_db

WATCHLIST_FILE = Path(__file__).parent.parent / "watchlist.json"


def _ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            ticker      TEXT PRIMARY KEY,
            layer       TEXT NOT NULL DEFAULT 'core',   -- core | theme | discovery
            active      INTEGER NOT NULL DEFAULT 1,
            cooldown_until TEXT,                        -- ISO-8601 or NULL
            added_ts    TEXT NOT NULL,
            note        TEXT
        )
    """)


def sync_from_file(path: Path | None = None) -> list[str]:
    """Load tickers from watchlist.json and upsert into SQLite. Returns active list."""
    src = path or WATCHLIST_FILE
    raw = json.loads(src.read_text(encoding="utf-8"))
    tickers = [t.upper().strip() for t in raw["tickers"]]

    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()

    init_db()
    with get_conn() as conn:
        _ensure_table(conn)
        for ticker in tickers:
            conn.execute(
                """INSERT INTO watchlist (ticker, active, added_ts)
                   VALUES (?, 1, ?)
                   ON CONFLICT(ticker) DO UPDATE SET active=1""",
                (ticker, ts),
            )
    return tickers


def get_active() -> list[str]:
    """Return active tickers from SQLite watchlist."""
    with get_conn() as conn:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT ticker FROM watchlist WHERE active=1 ORDER BY ticker"
        ).fetchall()
    return [r["ticker"] for r in rows]


def get_groups(path: Path | None = None) -> dict[str, str]:
    """Return {ticker: group} mapping from watchlist.json."""
    src = path or WATCHLIST_FILE
    raw = json.loads(src.read_text(encoding="utf-8"))
    return {t.upper(): g for t, g in raw.get("groups", {}).items()}


if __name__ == "__main__":
    tickers = sync_from_file()
    print(f"Synced {len(tickers)} tickers: {tickers}")
