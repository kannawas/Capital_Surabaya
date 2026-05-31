"""NAV, TWR, and alpha computation."""

from __future__ import annotations
from datetime import datetime, timezone

import yfinance as yf

from .db import get_conn
from .positions import get_cash, get_positions


def _fetch_price(ticker: str) -> float:
    t = yf.Ticker(ticker)
    hist = t.history(period="2d")
    if hist.empty:
        raise ValueError(f"No price data for {ticker}")
    return float(hist["Close"].iloc[-1])


def compute_nav(update_prices: bool = True, save_snapshot: bool = False) -> dict:
    """
    Compute current NAV = cash + sum(shares × last_price).

    If update_prices=True, fetch live prices from yfinance and update positions table.
    If save_snapshot=True, persist to nav_history (call once at end-of-run only).
    Returns dict with nav, cash, equity, and per-position breakdown.
    """
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
                price = pos["last_price"]  # fall back to last known
            with get_conn() as conn:
                conn.execute(
                    "UPDATE positions SET last_price=?, updated_ts=? WHERE ticker=?",
                    (price, ts, ticker),
                )
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
        _save_nav_snapshot(nav, cash, equity, ts)

    return {
        "nav": nav,
        "cash": round(cash, 2),
        "equity": round(equity, 2),
        "positions": breakdown,
        "ts": ts,
    }


def _save_nav_snapshot(nav: float, cash: float, equity: float, ts: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO nav_history (ts, nav, cash, equity) VALUES (?,?,?,?)",
            (ts, nav, cash, equity),
        )


def compute_twr() -> float | None:
    """
    Time-weighted return from nav_history.
    TWR = product of (NAV_end / NAV_start_of_subperiod) across all sub-periods
    separated by cash injections. Simple version: (current_nav / first_nav) - 1.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT nav FROM nav_history ORDER BY nav_id"
        ).fetchall()

    if len(rows) < 2:
        return None

    first_nav = rows[0]["nav"]
    last_nav = rows[-1]["nav"]
    if first_nav == 0:
        return None

    return round((last_nav / first_nav) - 1, 6)


def compute_alpha(benchmark: str = "SPY") -> dict | None:
    """
    Compute alpha vs benchmark over the same period as nav_history.
    Returns dict with portfolio_return, benchmark_return, alpha.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ts, nav FROM nav_history ORDER BY nav_id"
        ).fetchall()

    if len(rows) < 2:
        return None

    start_ts = rows[0]["ts"][:10]
    end_ts = rows[-1]["ts"][:10]

    try:
        hist = yf.download(benchmark, start=start_ts, end=end_ts, progress=False, auto_adjust=True)
        if hist.empty or len(hist) < 2:
            return None
        b_start = float(hist["Close"].iloc[0])
        b_end = float(hist["Close"].iloc[-1])
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
