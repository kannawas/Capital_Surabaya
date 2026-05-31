"""Order recording, fill computation, and gap rule."""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from .db import get_conn

SLIPPAGE_BPS = 5  # 0.05% per fill


@dataclass
class OrderResult:
    ticker: str
    side: Literal["buy", "sell"]
    shares: int
    status: Literal["filled", "missed", "skipped"]
    fill_price: float | None
    slippage: float | None
    cash_delta: float  # negative = cash out, positive = cash in
    intended_pct: float
    limit_price: float
    is_market: bool = False  # True for sell_or_avoid forced sells (no gap rule)


def record_order(
    run_id: int,
    ticker: str,
    side: Literal["buy", "sell"],
    intended_pct: float,
    limit_price: float,
    shares: int,
    open_price: float,  # actual market open / current price at execution
    ts: str | None = None,
    is_market: bool = False,  # sell_or_avoid resolutions use market fill, no gap rule
) -> OrderResult:
    """
    Record an order and update cash + positions.

    Gap rule (buys only): if open_price > limit_price, mark as MISSED.
    Sells from sell_or_avoid resolution are market orders — always fill.
    """
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()

    missed = (not is_market) and _is_gap(side, limit_price, open_price)

    if missed:
        result = OrderResult(
            ticker=ticker,
            side=side,
            shares=shares,
            status="missed",
            fill_price=None,
            slippage=None,
            cash_delta=0.0,
            intended_pct=intended_pct,
            limit_price=limit_price,
            is_market=is_market,
        )
    else:
        slip = open_price * SLIPPAGE_BPS / 10_000
        fill_price = open_price + slip if side == "buy" else open_price - slip
        fill_price = round(fill_price, 4)
        cash_delta = -shares * fill_price if side == "buy" else shares * fill_price
        result = OrderResult(
            ticker=ticker,
            side=side,
            shares=shares,
            status="filled",
            fill_price=fill_price,
            slippage=slip,
            cash_delta=round(cash_delta, 2),
            intended_pct=intended_pct,
            limit_price=limit_price,
            is_market=is_market,
        )

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO orders
               (run_id, ticker, side, intended_pct, limit_price, shares,
                status, fill_price, slippage, ts)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id, ticker, side, intended_pct, limit_price, shares,
                result.status, result.fill_price, result.slippage, ts,
            ),
        )
        if result.status == "filled":
            _update_position(conn, ticker, side, shares, result.fill_price, ts)
            conn.execute(
                "INSERT INTO cash_ledger (run_id, delta, reason, ts) VALUES (?,?,?,?)",
                (run_id, result.cash_delta, "fill", ts),
            )

    return result


def _is_gap(side: str, limit_price: float, open_price: float) -> bool:
    """Return True if a buy order cannot be filled due to gap above limit."""
    if side == "buy":
        return open_price > limit_price
    return False  # sells use market fill — no gap rule


def _update_position(
    conn, ticker: str, side: str, shares: int, fill_price: float, ts: str
) -> None:
    row = conn.execute(
        "SELECT shares, avg_cost FROM positions WHERE ticker=?", (ticker,)
    ).fetchone()

    if side == "buy":
        if row is None:
            conn.execute(
                "INSERT INTO positions (ticker, shares, avg_cost, last_price, updated_ts) VALUES (?,?,?,?,?)",
                (ticker, shares, fill_price, fill_price, ts),
            )
        else:
            old_shares, old_cost = row["shares"], row["avg_cost"]
            new_shares = old_shares + shares
            new_cost = (old_shares * old_cost + shares * fill_price) / new_shares
            conn.execute(
                "UPDATE positions SET shares=?, avg_cost=?, last_price=?, updated_ts=? WHERE ticker=?",
                (new_shares, round(new_cost, 4), fill_price, ts, ticker),
            )
    else:  # sell
        if row is None:
            return  # nothing to sell (skipped case handled upstream)
        new_shares = row["shares"] - shares
        if new_shares <= 0:
            conn.execute("DELETE FROM positions WHERE ticker=?", (ticker,))
        else:
            conn.execute(
                "UPDATE positions SET shares=?, last_price=?, updated_ts=? WHERE ticker=?",
                (new_shares, fill_price, ts, ticker),
            )
