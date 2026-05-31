"""
Macro indicator fetcher — VIX, 10Y yield, DXY via yfinance.

VIX  → ^VIX
10Y  → ^TNX  (value is yield in %, e.g. 4.35)
DXY  → DX-Y.NYB
"""

from __future__ import annotations
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

MACRO_TICKERS = {
    "vix": "^VIX",
    "yield_10y": "^TNX",
    "dxy": "DX-Y.NYB",
}


def fetch_macro(cutoff_ts: str | None = None) -> dict:
    """
    Fetch latest macro readings.

    Returns:
        {
            "vix":       {"value": float, "ts": str},
            "yield_10y": {"value": float, "ts": str},
            "dxy":       {"value": float, "ts": str},
            "regime":    "risk_on" | "risk_off" | "neutral",
            "event_lock": bool,   # True if VIX spike > threshold
        }

    All ts fields are UTC ISO-8601 representing the bar's close time.
    Records are pre-filtered to <= cutoff_ts so caller can run look-ahead guard.
    """
    readings = {}
    cutoff = _parse_cutoff(cutoff_ts)

    for key, symbol in MACRO_TICKERS.items():
        try:
            hist = yf.Ticker(symbol).history(period="5d", auto_adjust=True)
            if hist.empty:
                readings[key] = None
                continue
            # keep only bars <= cutoff
            hist.index = hist.index.tz_localize("UTC") if hist.index.tzinfo is None else hist.index.tz_convert("UTC")
            hist = hist[hist.index <= cutoff]
            if hist.empty:
                readings[key] = None
                continue
            last_bar = hist.iloc[-1]
            readings[key] = {
                "value": round(float(last_bar["Close"]), 4),
                "ts": hist.index[-1].isoformat(),
            }
        except Exception:
            readings[key] = None

    regime, event_lock = _classify_regime(readings)
    readings["regime"] = regime
    readings["event_lock"] = event_lock

    return readings


def _parse_cutoff(cutoff_ts: str | None) -> pd.Timestamp:
    if cutoff_ts is None:
        return pd.Timestamp.now(tz="UTC")
    dt = datetime.fromisoformat(cutoff_ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return pd.Timestamp(dt)


def _classify_regime(readings: dict) -> tuple[str, bool]:
    """
    Simple regime classification:
      - risk_off : VIX >= 25 OR 10Y yield >= 5.0
      - risk_on  : VIX < 18 AND yield < 4.5
      - neutral  : otherwise
    Event lock: VIX >= 30 (extreme fear — freeze new buys).
    """
    vix_val = (readings.get("vix") or {}).get("value")
    yield_val = (readings.get("yield_10y") or {}).get("value")

    event_lock = (vix_val is not None and vix_val >= 30)

    if vix_val is None:
        return "neutral", event_lock

    if vix_val >= 25 or (yield_val is not None and yield_val >= 5.0):
        regime = "risk_off"
    elif vix_val < 18 and (yield_val is None or yield_val < 4.5):
        regime = "risk_on"
    else:
        regime = "neutral"

    return regime, event_lock
