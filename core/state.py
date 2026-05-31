"""
Persistent conviction + thesis verdict store (SQLite).

Used when Execution or Fundamental agent hasn't run yet:
  - conviction  → carry forward yesterday's value, or state "not_reviewed"
  - verdict     → carry forward, or state "not_reviewed"
"""

from __future__ import annotations
from datetime import datetime, timezone

from ledger.db import get_conn


def _ensure_tables(conn) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS conviction_store (
            ticker      TEXT PRIMARY KEY,
            conviction  REAL,
            status      TEXT NOT NULL DEFAULT 'not_reviewed',
            run_ts      TEXT NOT NULL,
            source      TEXT NOT NULL DEFAULT 'execution_agent'
        );

        CREATE TABLE IF NOT EXISTS verdict_store (
            ticker      TEXT PRIMARY KEY,
            verdict     TEXT NOT NULL DEFAULT 'not_reviewed',
            run_ts      TEXT NOT NULL,
            source      TEXT NOT NULL DEFAULT 'fundamental_agent'
        );
    """)


def save_conviction(ticker: str, conviction: float, run_ts: str | None = None, source: str = "execution_agent") -> None:
    ts = run_ts or datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        _ensure_tables(conn)
        conn.execute(
            """INSERT INTO conviction_store (ticker, conviction, status, run_ts, source)
               VALUES (?, ?, 'reviewed', ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                 conviction=excluded.conviction,
                 status='reviewed',
                 run_ts=excluded.run_ts,
                 source=excluded.source""",
            (ticker.upper(), conviction, ts, source),
        )


def get_conviction(ticker: str) -> tuple[float | None, str]:
    """
    Returns (conviction_value, status).
    status: 'reviewed' | 'not_reviewed'
    conviction_value: float if reviewed, None if not_reviewed.
    """
    with get_conn() as conn:
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT conviction, status FROM conviction_store WHERE ticker=?",
            (ticker.upper(),),
        ).fetchone()
    if row is None:
        return None, "not_reviewed"
    return row["conviction"], row["status"]


def save_thesis_verdict(ticker: str, verdict: str, run_ts: str | None = None, source: str = "fundamental_agent") -> None:
    valid = {"intact", "weakening", "watch", "broken"}
    if verdict not in valid:
        raise ValueError(f"Invalid verdict {verdict!r}. Must be one of {valid}")
    ts = run_ts or datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        _ensure_tables(conn)
        conn.execute(
            """INSERT INTO verdict_store (ticker, verdict, run_ts, source)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                 verdict=excluded.verdict,
                 run_ts=excluded.run_ts,
                 source=excluded.source""",
            (ticker.upper(), verdict, ts, source),
        )


def get_thesis_verdict(ticker: str) -> str:
    """Returns verdict string: 'intact'|'weakening'|'watch'|'broken'|'not_reviewed'."""
    with get_conn() as conn:
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT verdict FROM verdict_store WHERE ticker=?",
            (ticker.upper(),),
        ).fetchone()
    return row["verdict"] if row else "not_reviewed"


def load_all_convictions(tickers: list[str]) -> dict[str, float | None]:
    """Return {ticker: conviction_or_None} for all given tickers."""
    return {t: get_conviction(t)[0] for t in tickers}


def load_all_verdicts(tickers: list[str]) -> dict[str, str]:
    """Return {ticker: verdict_str} for all given tickers."""
    return {t: get_thesis_verdict(t) for t in tickers}


def load_all_verdicts_with_ts(tickers: list[str]) -> dict[str, dict]:
    """
    Return {ticker: {"verdict": str, "run_ts": str | None}} for all given tickers.
    run_ts is None for tickers with no prior verdict (not_reviewed).
    """
    result = {}
    with get_conn() as conn:
        _ensure_tables(conn)
        for ticker in tickers:
            row = conn.execute(
                "SELECT verdict, run_ts FROM verdict_store WHERE ticker=?",
                (ticker.upper(),),
            ).fetchone()
            if row:
                result[ticker] = {"verdict": row["verdict"], "run_ts": row["run_ts"]}
            else:
                result[ticker] = {"verdict": "not_reviewed", "run_ts": None}
    return result
