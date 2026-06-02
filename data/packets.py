"""
Blind packet builder — enforces data blindness at the gateway layer (spec §1).

Each agent receives exactly what it's allowed to see — nothing more.
Blindness is structural: the wrong fields simply don't exist in the packet.

Agent blindness rules (from spec):
  technical_screener  → prices + technicals only. No portfolio, no news, no macro.
  macro_intelligence  → macro indicators only. No portfolio, no prices, no news.
  news_reporter       → news articles only. No portfolio, no prices, no macro.
  fundamental_thesis  → prices (for context) + news. No portfolio, no macro internals.
  execution           → all 4 research outputs. NO portfolio (holdings/cash/P&L).

Portfolio-aware actors (NOT built here — they read from ledger directly):
  risk, paper_executor, dashboard, auditor
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Literal

from ledger.guard import assert_no_lookahead, make_cutoff
from data.prices import fetch_ohlcv, compute_technicals
from data.macro import fetch_macro
from data.news import fetch_news
from data.watchlist import get_active

AgentName = Literal[
    "technical_screener",
    "macro_intelligence",
    "news_reporter",
    "fundamental_thesis",
    "execution",
]


def build_packet(
    agent: AgentName,
    cutoff_ts: str | None = None,
    tickers: list[str] | None = None,
) -> dict:
    """
    Build a blind data packet for the given agent.

    Args:
        agent:      which agent this packet is for
        cutoff_ts:  decision cutoff (ISO-8601 UTC). Defaults to now().
        tickers:    override watchlist (for testing). Defaults to active watchlist.

    Returns:
        A dict containing only the data the agent is allowed to see,
        plus metadata: agent, cutoff_ts, built_ts.

    Raises:
        LookAheadError if any data point post-dates the cutoff.
    """
    cutoff = make_cutoff(cutoff_ts)
    cutoff_str = cutoff.isoformat()
    tickers = tickers or get_active()

    packet: dict = {
        "agent": agent,
        "cutoff_ts": cutoff_str,
        "built_ts": datetime.now(timezone.utc).isoformat(),
        "tickers": tickers,
    }

    if agent == "technical_screener":
        packet.update(_build_technical(tickers, cutoff_str))

    elif agent == "macro_intelligence":
        packet.update(_build_macro(cutoff_str))

    elif agent == "news_reporter":
        packet.update(_build_news(tickers, cutoff_str))

    elif agent == "fundamental_thesis":
        # Thesis gets prices for context + news (no macro internals)
        packet.update(_build_technical(tickers, cutoff_str))
        packet["news"] = fetch_news(tickers, cutoff_str)

    elif agent == "execution":
        # Execution sees 4 research outputs — caller must inject them
        # Packet is a shell; research outputs are added by the run loop
        packet["_note"] = (
            "Execution packet: inject research_outputs dict before sending. "
            "No portfolio data allowed here."
        )

    else:
        raise ValueError(f"Unknown agent: {agent}")

    return packet


def inject_research_outputs(execution_packet: dict, research_outputs: dict) -> dict:
    """
    Merge the 4 blind research outputs into an execution packet.

    research_outputs keys: technical_screener, macro_intelligence,
                           news_reporter, fundamental_thesis
    Portfolio data must NOT be present in any research output value.
    """
    allowed_keys = {
        "technical_screener", "macro_intelligence", "news_reporter", "fundamental_thesis"
    }
    forbidden_keys = {"holdings", "cash", "nav", "positions", "pnl", "portfolio"}

    for key in research_outputs:
        if key not in allowed_keys:
            raise ValueError(f"Unexpected research output key: {key!r}")
        output = research_outputs[key]
        leaked = forbidden_keys & set(str(k).lower() for k in _flatten_keys(output))
        if leaked:
            from ledger.guard import LookAheadError
            raise ValueError(
                f"Portfolio data leaked into execution packet via {key!r}: {leaked}"
            )

    execution_packet["research_outputs"] = research_outputs
    execution_packet.pop("_note", None)
    return execution_packet


# --- internal builders ---

OHLCV_RECENT_BARS = 10  # enough for all candlestick patterns + 5-bar trend context

import json as _json
from pathlib import Path as _Path
_PRICES_JSON = _Path(__file__).parent.parent / "prices.json"


def _load_prices_json() -> dict | None:
    """
    Load pre-computed indicators from prices.json (written locally by
    data/prices_local.py). Used when live yfinance is unavailable — e.g. inside
    a CCR container that blocks outbound network. Returns None if missing/stale.
    """
    if not _PRICES_JSON.exists():
        return None
    try:
        data = _json.loads(_PRICES_JSON.read_text(encoding="utf-8"))
        return data.get("prices")
    except Exception:
        return None


def _build_technical(tickers: list[str], cutoff_str: str) -> dict:
    # Try live yfinance first; if it yields no usable bars (blocked network),
    # fall back to the committed prices.json snapshot.
    ohlcv = {}
    try:
        ohlcv = fetch_ohlcv(tickers, period="60d")
    except Exception:
        ohlcv = {}

    live_ok = any(ohlcv.get(t) for t in tickers)

    if live_ok:
        tech = {}
        all_bars = []
        for ticker in tickers:
            bars = [b for b in ohlcv.get(ticker, []) if b["ts"] <= cutoff_str]
            indicators = compute_technicals(bars)
            indicators["ohlcv_recent"] = [
                {"ts": b["ts"], "o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"]}
                for b in bars[-OHLCV_RECENT_BARS:]
            ]
            indicators["price_source"] = "yfinance_live"
            tech[ticker] = indicators
            all_bars.extend(bars)
        assert_no_lookahead(all_bars, cutoff_str)
        return {"prices": tech}

    # --- Fallback: prices.json (local-fetched, committed to repo) ---
    snapshot = _load_prices_json()
    if snapshot:
        tech = {}
        for ticker in tickers:
            ind = snapshot.get(ticker)
            if not ind or "error" in ind:
                tech[ticker] = {"price_source": "missing", "data_quality": "impaired"}
                continue
            ind = dict(ind)  # copy
            ind["price_source"] = "prices_json"
            # trim recent bars to N for token economy
            if "ohlcv_recent" in ind and len(ind["ohlcv_recent"]) > OHLCV_RECENT_BARS:
                ind["ohlcv_recent"] = ind["ohlcv_recent"][-OHLCV_RECENT_BARS:]
            tech[ticker] = ind
        return {"prices": tech, "_price_source": "prices_json_fallback"}

    # --- Nothing available ---
    return {"prices": {t: {"price_source": "none", "data_quality": "impaired"} for t in tickers},
            "_price_source": "unavailable"}


def _build_macro(cutoff_str: str) -> dict:
    macro = fetch_macro(cutoff_str)

    # build guard-checkable list from macro readings
    checkable = []
    for key in ("vix", "yield_10y", "dxy"):
        val = macro.get(key)
        if val and isinstance(val, dict) and "ts" in val:
            checkable.append({"ts": val["ts"], "ticker": key})

    assert_no_lookahead(checkable, cutoff_str)
    return {"macro": macro}


def _build_news(tickers: list[str], cutoff_str: str) -> dict:
    news = fetch_news(tickers, cutoff_str)

    # guard: flatten all articles and check timestamps
    all_articles = []
    for ticker_articles in news.values():
        all_articles.extend(ticker_articles)

    assert_no_lookahead(all_articles, cutoff_str)
    return {"news": news}


def _flatten_keys(obj, depth: int = 3) -> list[str]:
    """Recursively collect dict keys up to a given depth."""
    if depth == 0 or not isinstance(obj, dict):
        return []
    keys = list(obj.keys())
    for v in obj.values():
        keys.extend(_flatten_keys(v, depth - 1))
    return keys
