"""
Data feed smoke tests — hits yfinance live (requires internet).
Verifies: prices, macro, news, packet builder, blindness enforcement.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os, tempfile
import ledger.db as _db
tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp.close()
_db.DB_PATH = Path(tmp.name)

from ledger.db import init_db
init_db()

from data.prices import fetch_ohlcv, compute_technicals, fetch_latest_price
from data.macro import fetch_macro
from data.news import fetch_news
from data.watchlist import sync_from_file, get_active
from data.packets import build_packet, inject_research_outputs
from ledger.guard import LookAheadError

CUTOFF = "2026-05-30T09:30:00+00:00"
TEST_TICKERS = ["MSFT", "NVDA"]


def test_prices():
    ohlcv = fetch_ohlcv(TEST_TICKERS, period="30d")
    for ticker in TEST_TICKERS:
        bars = ohlcv.get(ticker, [])
        assert len(bars) > 0, f"No bars for {ticker}"
        assert all("ts" in b and "close" in b for b in bars), "Missing fields"
        tech = compute_technicals(bars)
        assert "last_close" in tech and tech["last_close"] > 0
        print(f"  {ticker}: {len(bars)} bars, last_close={tech['last_close']}, RSI={tech['rsi_14']}")
    print("PASS: prices + technicals")


def test_macro():
    macro = fetch_macro(CUTOFF)
    assert "regime" in macro
    assert macro["regime"] in ("risk_on", "risk_off", "neutral")
    assert "event_lock" in macro
    for key in ("vix", "yield_10y", "dxy"):
        val = macro.get(key)
        if val:
            assert "value" in val and "ts" in val
            print(f"  {key}: {val['value']} @ {val['ts'][:10]}")
    print(f"  regime={macro['regime']}  event_lock={macro['event_lock']}")
    print("PASS: macro")


def test_news():
    news = fetch_news(TEST_TICKERS, CUTOFF)
    for ticker in TEST_TICKERS:
        articles = news.get(ticker, [])
        print(f"  {ticker}: {len(articles)} articles (cutoff {CUTOFF[:10]})")
        if articles:
            a = articles[0]
            assert "ts" in a and "title" in a
            # all must be before cutoff
            for art in articles:
                assert art["ts"] <= CUTOFF, f"Look-ahead leak: {art['ts']}"
    print("PASS: news (look-ahead filtered)")


def test_watchlist_sync():
    synced = sync_from_file()
    assert len(synced) > 0
    active = get_active()
    assert set(synced) == set(active)
    print(f"  Watchlist: {active}")
    print("PASS: watchlist sync")


def test_packet_technical():
    packet = build_packet("technical_screener", cutoff_ts=CUTOFF, tickers=TEST_TICKERS)
    assert packet["agent"] == "technical_screener"
    assert "prices" in packet
    assert "macro" not in packet
    assert "news" not in packet
    assert "holdings" not in packet
    msft = packet["prices"].get("MSFT", {})
    assert msft.get("last_close", 0) > 0
    print(f"  MSFT technicals: close={msft.get('last_close')}, RSI={msft.get('rsi_14')}")
    print("PASS: technical_screener packet (blind)")


def test_packet_macro():
    packet = build_packet("macro_intelligence", cutoff_ts=CUTOFF, tickers=TEST_TICKERS)
    assert packet["agent"] == "macro_intelligence"
    assert "macro" in packet
    assert "prices" not in packet
    assert "news" not in packet
    print(f"  Macro regime: {packet['macro']['regime']}")
    print("PASS: macro_intelligence packet (blind)")


def test_packet_news():
    packet = build_packet("news_reporter", cutoff_ts=CUTOFF, tickers=TEST_TICKERS)
    assert packet["agent"] == "news_reporter"
    assert "news" in packet
    assert "prices" not in packet
    assert "macro" not in packet
    print(f"  News articles: { {t: len(v) for t,v in packet['news'].items()} }")
    print("PASS: news_reporter packet (blind)")


def test_execution_blindness_enforced():
    """inject_research_outputs must reject portfolio data."""
    exec_packet = build_packet("execution", cutoff_ts=CUTOFF, tickers=TEST_TICKERS)
    try:
        inject_research_outputs(exec_packet, {
            "technical_screener": {"prices": {}},
            "macro_intelligence": {"macro": {}},
            "news_reporter": {"news": {}},
            "fundamental_thesis": {"verdict": "intact", "holdings": {"MSFT": 100}},  # leak!
        })
        assert False, "Should have raised ValueError for portfolio leak"
    except ValueError as e:
        assert "holdings" in str(e).lower() or "leaked" in str(e).lower()
        print(f"  Caught leak correctly: {e}")
    print("PASS: execution blindness enforcement")


if __name__ == "__main__":
    print("\n--- Prices ---")
    test_prices()
    print("\n--- Macro ---")
    test_macro()
    print("\n--- News ---")
    test_news()
    print("\n--- Watchlist ---")
    test_watchlist_sync()
    print("\n--- Packets ---")
    test_packet_technical()
    test_packet_macro()
    test_packet_news()
    print("\n--- Blindness ---")
    test_execution_blindness_enforced()
    print("\nAll data feed tests passed.")
    try:
        os.unlink(tmp.name)
    except Exception:
        pass
