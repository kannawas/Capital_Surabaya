"""NAV, TWR, and alpha computation — works in SQLite and Supabase mode."""

from __future__ import annotations
from datetime import datetime, timezone

import yfinance as yf

from ledger.storage import insert, select
from ledger.positions import get_cash, get_positions


def _fetch_price(ticker: str) -> float:
    hist = yf.Ticker(ticker).history(period="2d")
    if hist.empty:
        raise ValueError(f"No price data for {ticker}")
    return float(hist["Close"].iloc[-1])


def compute_nav(update_prices: bool = True, save_snapshot: bool = False) -> dict:
    """
    Compute current NAV = cash + sum(shares × last_price).

    If update_prices=True, fetch live prices and update positions table.
    If save_snapshot=True, persist to nav_history (call once at end-of-run only).
    """
    from ledger.storage import update as storage_update
    positions = get_positions()
    cash = get_cash()
    ts = datetime.now(timezone.utc).isoformat()

    equity = 0.0
    breakdown = []
    for pos in positions:
        ticker = pos["ticker"]
        if update_prices:
            try:
                price = _fetch_price(ticker)
            except Exception:
                price = pos["last_price"]
            storage_update("positions", {
                "last_price": price, "updated_ts": ts,
            }, where={"ticker": ticker})
        else:
            price = pos["last_price"]

        value = pos["shares"] * price
        equity += value
        breakdown.append({
            "ticker": ticker,
            "shares": pos["shares"],
            "price": price,
            "value": round(value, 2),
            "avg_cost": pos["avg_cost"],
            "unrealized_pnl": round(value - pos["shares"] * pos["avg_cost"], 2),
        })

    nav = round(cash + equity, 2)

    if save_snapshot:
        insert("nav_history", {
            "ts": ts, "nav": nav, "cash": round(cash, 2), "equity": round(equity, 2),
        })

    return {
        "nav": nav, "cash": round(cash, 2), "equity": round(equity, 2),
        "positions": breakdown, "ts": ts,
    }


def compute_twr() -> float | None:
    rows = select("nav_history", order_by="nav_id ASC")
    if len(rows) < 2:
        return None
    first_nav = rows[0]["nav"]
    last_nav  = rows[-1]["nav"]
    if first_nav == 0:
        return None
    return round((last_nav / first_nav) - 1, 6)


def compute_alpha(benchmark: str = "SPY") -> dict | None:
    rows = select("nav_history", order_by="nav_id ASC")
    if len(rows) < 2:
        return None

    start_ts = rows[0]["ts"][:10]
    end_ts   = rows[-1]["ts"][:10]

    try:
        hist = yf.download(benchmark, start=start_ts, end=end_ts,
                           progress=False, auto_adjust=True)
        if hist.empty or len(hist) < 2:
            return None
        b_start = float(hist["Close"].iloc[0])
        b_end   = float(hist["Close"].iloc[-1])
        benchmark_return = round((b_end / b_start) - 1, 6)
    except Exception:
        return None

    portfolio_return = compute_twr()
    if portfolio_return is None:
        return None

    return {
        "benchmark": benchmark,
        "portfolio_return": portfolio_return,
        "benchmark_return": benchmark_return,
        "alpha": round(portfolio_return - benchmark_return, 6),
    }
