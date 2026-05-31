"""
Price fetcher — OHLCV + basic technicals via yfinance.

Returns timestamped records safe to pass through look-ahead guard.
All timestamps are UTC and represent the bar's close time.
"""

from __future__ import annotations
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf


def _bar_ts(dt_index_value) -> str:
    """Convert a pandas Timestamp (tz-aware or naive) to UTC ISO-8601."""
    ts = pd.Timestamp(dt_index_value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.isoformat()


def fetch_ohlcv(tickers: list[str], period: str = "60d") -> dict[str, list[dict]]:
    """
    Fetch daily OHLCV for each ticker.

    Returns:
        {ticker: [{"ts": str, "open": float, "high": float, "low": float,
                   "close": float, "volume": int}, ...]}
    """
    result: dict[str, list[dict]] = {}

    for ticker in tickers:
        try:
            hist = yf.Ticker(ticker).history(period=period, auto_adjust=True)
            if hist.empty:
                continue
            bars = []
            for idx, row in hist.iterrows():
                bars.append({
                    "ts": _bar_ts(idx),
                    "open": round(float(row["Open"]), 4),
                    "high": round(float(row["High"]), 4),
                    "low": round(float(row["Low"]), 4),
                    "close": round(float(row["Close"]), 4),
                    "volume": int(row["Volume"]),
                })
            result[ticker] = bars
        except Exception as e:
            result[ticker] = []

    return result


def compute_technicals(bars: list[dict]) -> dict:
    """
    Compute technical indicators from OHLCV bars.

    Returns a flat dict of the most recent values:
        sma_20, sma_50, ema_20, ema_50, rsi_14, atr_14, bb_upper, bb_mid, bb_lower,
        bb_pct_b, volume_avg_20, last_close, pct_change_1d, pct_change_5d
    """
    if not bars:
        return {}

    closes = pd.Series([b["close"] for b in bars])
    highs  = pd.Series([b["high"]  for b in bars])
    lows   = pd.Series([b["low"]   for b in bars])
    vols   = pd.Series([b["volume"] for b in bars])

    # --- moving averages ---
    sma_20 = float(closes.rolling(20).mean().iloc[-1]) if len(closes) >= 20 else None
    sma_50 = float(closes.rolling(50).mean().iloc[-1]) if len(closes) >= 50 else None
    ema_20 = float(closes.ewm(span=20, adjust=False).mean().iloc[-1]) if len(closes) >= 20 else None
    ema_50 = float(closes.ewm(span=50, adjust=False).mean().iloc[-1]) if len(closes) >= 50 else None

    # --- RSI ---
    rsi_14 = _rsi(closes, 14)

    # --- ATR ---
    tr = pd.concat([
        highs - lows,
        (highs - closes.shift()).abs(),
        (lows  - closes.shift()).abs(),
    ], axis=1).max(axis=1)
    atr_14 = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else None

    # --- Bollinger Bands (20-period, 2 std) ---
    bb_mid_s = closes.rolling(20).mean()
    bb_std_s = closes.rolling(20).std(ddof=0)
    bb_upper_val = float((bb_mid_s + 2 * bb_std_s).iloc[-1]) if len(closes) >= 20 else None
    bb_mid_val   = float(bb_mid_s.iloc[-1])                   if len(closes) >= 20 else None
    bb_lower_val = float((bb_mid_s - 2 * bb_std_s).iloc[-1]) if len(closes) >= 20 else None

    last = float(closes.iloc[-1])
    # %B = (price - lower) / (upper - lower); 0=at lower, 1=at upper
    if bb_upper_val and bb_lower_val and (bb_upper_val - bb_lower_val) > 0:
        bb_pct_b = round((last - bb_lower_val) / (bb_upper_val - bb_lower_val), 4)
    else:
        bb_pct_b = None

    # --- volume ---
    volume_avg_20 = float(vols.rolling(20).mean().iloc[-1]) if len(vols) >= 20 else None
    last_volume   = int(vols.iloc[-1]) if not vols.empty else None
    volume_ratio  = round(last_volume / volume_avg_20, 4) if volume_avg_20 and last_volume else None

    # --- momentum ---
    pct_1d = float((closes.iloc[-1] / closes.iloc[-2]  - 1) * 100) if len(closes) >= 2  else None
    pct_5d = float((closes.iloc[-1] / closes.iloc[-6]  - 1) * 100) if len(closes) >= 6  else None

    def _r(v): return round(v, 4) if v is not None else None

    return {
        "last_close":    round(last, 4),
        "sma_20":        _r(sma_20),
        "sma_50":        _r(sma_50),
        "ema_20":        _r(ema_20),
        "ema_50":        _r(ema_50),
        "rsi_14":        _r(rsi_14),
        "atr_14":        _r(atr_14),
        "bb_upper":      _r(bb_upper_val),
        "bb_mid":        _r(bb_mid_val),
        "bb_lower":      _r(bb_lower_val),
        "bb_pct_b":      bb_pct_b,
        "volume_avg_20": _r(volume_avg_20),
        "volume_ratio":  volume_ratio,
        "pct_change_1d": _r(pct_1d),
        "pct_change_5d": _r(pct_5d),
        "ts": bars[-1]["ts"],
    }


def _rsi(closes: pd.Series, period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    delta = closes.diff().dropna()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return None if pd.isna(val) else float(val)


def fetch_latest_price(ticker: str) -> float | None:
    """Convenience: fetch single latest close price."""
    try:
        hist = yf.Ticker(ticker).history(period="2d", auto_adjust=True)
        if hist.empty:
            return None
        return round(float(hist["Close"].iloc[-1]), 4)
    except Exception:
        return None
