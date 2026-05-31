"""Read and write positions and cash — works in both SQLite and Supabase mode."""

from __future__ import annotations
from datetime import datetime, timezone
from ledger.storage import select, insert, upsert, update, delete, scalar_sum


def get_positions() -> list[dict]:
    return select("positions", order_by="ticker ASC")


def get_cash() -> float:
    return round(scalar_sum("cash_ledger", "delta"), 2)


def seed_positions(holdings: list[dict], ts: str | None = None) -> None:
    """
    Seed existing holdings before the first run.
    Each dict: {"ticker": str, "shares": int, "avg_cost": float, "last_price": float}
    """
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    for h in holdings:
        upsert("positions", {
            "ticker":     h["ticker"].upper(),
            "shares":     h["shares"],
            "avg_cost":   h["avg_cost"],
            "last_price": h["last_price"],
            "updated_ts": ts,
        }, conflict_col="ticker")


def seed_cash(amount: float, reason: str = "injection", ts: str | None = None) -> None:
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    insert("cash_ledger", {"delta": amount, "reason": reason, "ts": ts})
