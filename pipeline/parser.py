"""
Parse agent JSON output blocks from raw response text.

Agents write human-readable analysis first, then a ```json block at the end.
We extract the LAST json block (the machine-readable output).

Also provides typed extractors for each agent's key fields.
"""

from __future__ import annotations
import json
import re


def extract_json(response: str) -> dict:
    """
    Extract the last ```json ... ``` block from an agent response.

    Raises:
        ValueError if no valid JSON block is found.
    """
    pattern = r"```json\s*([\s\S]*?)```"
    matches = re.findall(pattern, response)
    if not matches:
        raise ValueError("No ```json block found in agent response")
    raw = matches[-1].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON parse error in agent response: {e}\nRaw:\n{raw[:500]}")


# --- typed extractors per agent ---

def parse_technical(response: str) -> dict:
    """Extract technical screener output."""
    data = extract_json(response)
    return {
        "buy_candidates":  data.get("buy_candidates", []),
        "sell_or_avoid":   data.get("sell_or_avoid", []),
        "watch":           data.get("watch", []),
        "data_issues":     data.get("data_issues", []),
        "cutoff_ts":       data.get("cutoff_ts", ""),
    }


def parse_macro(response: str) -> dict:
    """Extract macro intelligence output. Returns fields consumed by core/limits.py."""
    data = extract_json(response)
    return {
        "regime":                   data.get("regime", "neutral"),
        "event_lock":               data.get("event_lock", False),
        "event_lock_reason":        data.get("event_lock_reason", ""),
        "confidence":               data.get("confidence", "medium"),
        "indicator_override":       data.get("indicator_override", False),
        "macro_events":             data.get("macro_events", []),
        "historical_study_invoked": data.get("historical_study_invoked", False),
        "preferred_groups":         data.get("preferred_groups", []),
        "reduce_groups":            data.get("reduce_groups", []),
        "lead_time_days":           data.get("lead_time_days"),
        "vix":                      data.get("vix", {}),
        "yield_10y":                data.get("yield_10y", {}),
    }


def parse_news(response: str) -> dict:
    """Extract news reporter output. Returns news_flagged and ticker_summary."""
    data = extract_json(response)
    return {
        "news_flagged":   data.get("news_flagged", []),
        "ticker_summary": data.get("ticker_summary", []),
        "clean_tickers":  data.get("clean_tickers", []),
        "events":         data.get("events", []),
    }


def parse_thesis(response: str) -> dict:
    """Extract fundamental thesis verdicts."""
    data = extract_json(response)
    verdicts = data.get("verdicts", [])
    # Build lookup: {ticker: verdict_str}
    verdict_map = {}
    for v in verdicts:
        ticker  = v.get("ticker", "")
        verdict = v.get("verdict", "not_reviewed")
        # confirmed_unchanged → keep as "confirmed_unchanged" for logging,
        # but the verdict_store already holds the real value so no overwrite needed
        if ticker:
            verdict_map[ticker] = verdict
    return {
        "verdict_map":    verdict_map,
        "verdicts_raw":   verdicts,
        "triggered_count": data.get("triggered_count", 0),
    }


def parse_execution(response: str) -> list[dict]:
    """
    Extract execution intents.

    Returns list of intent dicts compatible with core/run.py:
      [{ticker, side, conviction, limit_price, stop_loss, gate_fired, ...}]
    """
    data = extract_json(response)
    intents = data.get("intents", [])
    # Validate required fields
    for intent in intents:
        assert "ticker" in intent, f"Intent missing ticker: {intent}"
        assert intent.get("side") in ("buy", "sell_or_avoid", "defer", "avoid"), \
            f"Invalid side in intent: {intent}"
    return intents


def parse_watchlist_removals(response: str) -> list[dict]:
    """Extract watchlist removal actions from execution response."""
    data = extract_json(response)
    return data.get("watchlist_removals", [])
