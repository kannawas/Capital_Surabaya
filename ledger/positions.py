"""Read positions and cash from the ledger."""

from __future__ import annotations
from .db import get_conn


def get_positions() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ticker, shares, avg_cost, last_price, updated_ts FROM positions ORDER BY ticker"
        ).fetchall()
    return [dict(r) for r in rows]


def get_cash() -> float:
    """Sum all cash_ledger deltas."""
    with get_conn() as conn:
        row = conn.execute("SELECT COALESCE(SUM(delta), 0) as total FROM cash_ledger").fetchone()
    return round(row["total"], 2)


def seed_positions(holdings: list[dict], ts: str | None = None) -> None:
    """
    Seed existing holdings before the first run.

    Each dict: {"ticker": str, "shares": int, "avg_cost": float, "last_price": float}
    Overwrites any existing row for the ticker.
    """
    from datetime import datetime, timezone
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        for h in holdings:
            conn.execute(
                """INSERT OR REPLACE INTO positions
                   (ticker, shares, avg_cost, last_price, updated_ts)
                   VALUES (?,?,?,?,?)""",
                (h["ticker"], h["shares"], h["avg_cost"], h["last_price"], ts),
            )


def seed_cash(amount: float, reason: str = "injection", ts: str | None = None) -> None:
    """Insert an initial cash balance (capital injection)."""
    from datetime import datetime, timezone
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO cash_ledger (run_id, delta, reason, ts) VALUES (?,?,?,?)",
            (None, amount, reason, ts),
        )
