"""Order recording, fill computation, and gap rule — works in SQLite and Supabase mode."""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from ledger.storage import insert, select, update, delete

SLIPPAGE_BPS = 5  # 0.05% per fill


@dataclass
class OrderResult:
    ticker: str
    side: Literal["buy", "sell"]
    shares: int
    status: Literal["filled", "missed", "skipped"]
    fill_price: float | None
    slippage: float | None
    cash_delta: float
    intended_pct: float
    limit_price: float
    is_market: bool = False


def record_order(
    run_id: int,
    ticker: str,
    side: Literal["buy", "sell"],
    intended_pct: float,
    limit_price: float,
    shares: int,
    open_price: float,
    ts: str | None = None,
    is_market: bool = False,
) -> OrderResult:
    """
    Apply gap rule then record the order and update cash + positions.

    Gap rule (buys only): open_price > limit_price → MISSED.
    Sells from sell_or_avoid are market orders (is_market=True) — always fill.
    """
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()

    missed = (not is_market) and _is_gap(side, limit_price, open_price)

    if missed:
        result = OrderResult(
            ticker=ticker, side=side, shares=shares,
            status="missed", fill_price=None, slippage=None,
            cash_delta=0.0, intended_pct=intended_pct,
            limit_price=limit_price, is_market=is_market,
        )
    else:
        slip = open_price * SLIPPAGE_BPS / 10_000
        fill_price = open_price + slip if side == "buy" else open_price - slip
        fill_price = round(fill_price, 4)
        cash_delta = -shares * fill_price if side == "buy" else shares * fill_price
        result = OrderResult(
            ticker=ticker, side=side, shares=shares,
            status="filled", fill_price=fill_price, slippage=slip,
            cash_delta=round(cash_delta, 2), intended_pct=intended_pct,
            limit_price=limit_price, is_market=is_market,
        )

    # Write order record
    insert("orders", {
        "run_id": run_id, "ticker": ticker, "side": side,
        "intended_pct": intended_pct, "limit_price": limit_price,
        "shares": shares, "status": result.status,
        "fill_price": result.fill_price, "slippage": result.slippage,
        "ts": ts,
    })

    if result.status == "filled":
        _update_position(ticker, side, shares, result.fill_price, ts)
        insert("cash_ledger", {
            "run_id": run_id, "delta": result.cash_delta,
            "reason": "fill", "ts": ts,
        })

    return result


def _is_gap(side: str, limit_price: float, open_price: float) -> bool:
    if side == "buy":
        return open_price > limit_price
    return False  # sells use market fill


def _update_position(ticker: str, side: str, shares: int, fill_price: float, ts: str) -> None:
    rows = select("positions", where={"ticker": ticker})
    row = rows[0] if rows else None

    if side == "buy":
        if row is None:
            insert("positions", {
                "ticker": ticker, "shares": shares,
                "avg_cost": fill_price, "last_price": fill_price,
                "updated_ts": ts,
            })
        else:
            old_shares = row["shares"]
            old_cost   = row["avg_cost"]
            new_shares = old_shares + shares
            new_cost   = (old_shares * old_cost + shares * fill_price) / new_shares
            update("positions", {
                "shares": new_shares, "avg_cost": round(new_cost, 4),
                "last_price": fill_price, "updated_ts": ts,
            }, where={"ticker": ticker})
    else:  # sell
        if row is None:
            return
        new_shares = row["shares"] - shares
        if new_shares <= 0:
            delete("positions", where={"ticker": ticker})
        else:
            update("positions", {
                "shares": new_shares, "last_price": fill_price, "updated_ts": ts,
            }, where={"ticker": ticker})
