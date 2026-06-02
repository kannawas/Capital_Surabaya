"""
Local price fetcher — runs on the user's machine (where yfinance works) and
writes prices.json for the CCR routine to consume.

CCR blocks all outbound network, so it can't fetch OHLC itself. This script
runs locally, fetches 60-day OHLCV for the active watchlist, and writes
prices.json which is committed to the repo. The packet builder reads it as a
fallback when live yfinance is unavailable (i.e. inside CCR).

Usage:
    python3 data/prices_local.py          # fetch active watchlist -> prices.json
"""

from __future__ import annotations
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("USE_SUPABASE", "false")

OUT = Path(__file__).parent.parent / "prices.json"


def fetch_and_write() -> dict:
    from data.watchlist import sync_from_file
    from data.prices import fetch_ohlcv, compute_technicals
    from ledger.storage import select, get_conn

    # Sync watchlist.json → ledger (replace, not just upsert, so removed tickers disappear)
    import json as _json
    wl_path = Path(__file__).parent.parent / "watchlist.json"
    wl_tickers = [t.upper() for t in _json.loads(wl_path.read_text(encoding="utf-8")).get("tickers", [])]

    # Hard-sync: delete all watchlist rows then re-insert from file
    with get_conn() as conn:
        conn.execute("DELETE FROM watchlist")
    sync_from_file()   # re-inserts only current tickers

    # Add portfolio holdings (mandatory — must always have prices for monitoring)
    positions = select("positions")
    port_tickers = [p["ticker"] for p in positions if p["ticker"] not in wl_tickers]

    tickers = wl_tickers + port_tickers
    print(f"Universe: {len(wl_tickers)} tickers | Portfolio: {len(port_tickers)} extra | Total: {len(tickers)}")

    ohlcv = fetch_ohlcv(tickers, period="90d")  # 90d gives buffer for SMA50/200

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "yfinance (local)",
        "tickers": tickers,
        "prices": {},        # ticker -> recent computed technicals + bars
    }

    OHLCV_RECENT = 60  # enough bars for all indicators in the cloud
    for ticker in tickers:
        bars = ohlcv.get(ticker, [])
        if not bars:
            payload["prices"][ticker] = {"error": "no_data"}
            continue
        tech = compute_technicals(bars)
        tech["ohlcv_recent"] = [
            {"ts": b["ts"], "o": b["open"], "h": b["high"],
             "l": b["low"], "c": b["close"], "v": b["volume"]}
            for b in bars[-OHLCV_RECENT:]
        ]
        payload["prices"][ticker] = tech

    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    ok = sum(1 for v in payload["prices"].values() if "error" not in v)
    return {"written": str(OUT), "ok_tickers": ok, "total": len(tickers),
            "fetched_at": payload["fetched_at"]}


if __name__ == "__main__":
    r = fetch_and_write()
    print(f"prices.json written: {r['ok_tickers']}/{r['total']} tickers @ {r['fetched_at']}")
