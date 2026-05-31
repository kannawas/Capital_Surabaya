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


def _build_technical(tickers: list[str], cutoff_str: str) -> dict:
    ohlcv = fetch_ohlcv(tickers, period="60d")
    tech = {}
    all_bars = []
    for ticker in tickers:
        bars = ohlcv.get(ticker, [])
        # filter to cutoff (look-ahead gate)
        bars = [b for b in bars if b["ts"] <= cutoff_str]
        indicators = compute_technicals(bars)
        # attach last N bars for candlestick module — strip volume to save tokens
        indicators["ohlcv_recent"] = [
            {"ts": b["ts"], "o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"]}
            for b in bars[-OHLCV_RECENT_BARS:]
        ]
        tech[ticker] = indicators
        all_bars.extend(bars)

    # look-ahead guard on all price bars
    assert_no_lookahead(all_bars, cutoff_str)

    return {"prices": tech}


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
