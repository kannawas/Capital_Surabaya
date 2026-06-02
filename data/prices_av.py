"""
Alpha Vantage price fetcher — fallback for environments where yfinance is blocked.

Alpha Vantage exposes a plain HTTPS REST API (https://www.alphavantage.co/query)
which may be reachable from CCR containers that block yfinance.

Free tier: 25 requests/day, 5 requests/minute.
With a 10-ticker watchlist that's tight — we fetch daily bars (1 req/ticker) and
cache aggressively. For more headroom, upgrade the key or reduce the watchlist.

Set ALPHAVANTAGE_KEY in the environment.

Returns the SAME bar format as data.prices.fetch_ohlcv so it's a drop-in:
    {ticker: [{"ts", "open", "high", "low", "close", "volume"}, ...]}
"""

from __future__ import annotations
import os
import time
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone

BASE = "https://www.alphavantage.co/query"
RATE_DELAY_S = 13  # free tier: 5 req/min -> ~12s apart; use 13 for safety


def _get(params: dict) -> dict:
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "capital-surabaya/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def fetch_ohlcv_av(tickers: list[str], outputsize: str = "compact",
                   api_key: str | None = None) -> dict[str, list[dict]]:
    """
    Fetch daily OHLCV via Alpha Vantage TIME_SERIES_DAILY.

    outputsize: "compact" (last 100 bars) or "full" (20+ yrs).
    Respects the 5 req/min rate limit by sleeping between calls.
    """
    key = api_key or os.environ.get("ALPHAVANTAGE_KEY", "")
    if not key:
        raise RuntimeError("ALPHAVANTAGE_KEY not set")

    result: dict[str, list[dict]] = {}
    for i, ticker in enumerate(tickers):
        if i > 0:
            time.sleep(RATE_DELAY_S)  # rate limit
        try:
            data = _get({
                "function": "TIME_SERIES_DAILY",
                "symbol": ticker,
                "outputsize": outputsize,
                "apikey": key,
            })
            series = data.get("Time Series (Daily)")
            if not series:
                # API note / rate-limit / error message
                result[ticker] = []
                continue
            bars = []
            for date_str, vals in sorted(series.items()):
                # date_str is YYYY-MM-DD (US market date) -> treat as 04:00 UTC close
                ts = f"{date_str}T21:00:00+00:00"  # ~US close in UTC
                bars.append({
                    "ts": ts,
                    "open": round(float(vals["1. open"]), 4),
                    "high": round(float(vals["2. high"]), 4),
                    "low": round(float(vals["3. low"]), 4),
                    "close": round(float(vals["4. close"]), 4),
                    "volume": int(float(vals["5. volume"])),
                })
            result[ticker] = bars
        except Exception as e:
            result[ticker] = []
    return result


def test_connectivity(api_key: str | None = None) -> dict:
    """Quick single-ticker test to confirm the endpoint is reachable and key works."""
    key = api_key or os.environ.get("ALPHAVANTAGE_KEY", "demo")
    try:
        data = _get({
            "function": "TIME_SERIES_DAILY",
            "symbol": "IBM",
            "outputsize": "compact",
            "apikey": key,
        })
        if "Time Series (Daily)" in data:
            n = len(data["Time Series (Daily)"])
            return {"ok": True, "bars": n, "msg": f"OK — {n} bars for IBM"}
        # Return whatever message AV gave (rate limit / invalid key / note)
        return {"ok": False, "msg": str(data)[:300]}
    except Exception as e:
        return {"ok": False, "msg": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    import sys
    key = sys.argv[1] if len(sys.argv) > 1 else None
    print(test_connectivity(key))
