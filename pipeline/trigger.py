"""
Triggered tickers computation for Fundamental Thesis Agent.

A ticker is triggered (requires full analysis this run) if ANY of:
  1. No prior verdict             — verdict is "not_reviewed"
  2. News-flagged this run        — appears in news_reporter news_flagged list
  3. Verdict stale (> 14 days)    — run_ts is older than max_age_days
  4. Manual request               — caller passes it in force_tickers

Tickers NOT triggered get "confirmed_unchanged" in the thesis packet —
the agent confirms the prior verdict without re-analyzing.
"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone


VERDICT_MAX_AGE_DAYS = 14


def compute_triggered_tickers(
    tickers: list[str],
    verdicts_with_ts: dict[str, dict],
    news_flagged: list[str],
    force_tickers: list[str] | None = None,
    max_age_days: int = VERDICT_MAX_AGE_DAYS,
    as_of: str | None = None,
) -> dict[str, list[str]]:
    """
    Compute which tickers require full Fundamental Thesis analysis.

    Args:
        tickers:          full active watchlist
        verdicts_with_ts: {ticker: {"verdict": str, "run_ts": str|None}}
                          from core.state.load_all_verdicts_with_ts()
        news_flagged:     tickers flagged by News Reporter this run
        force_tickers:    additional tickers to force-trigger (manual request)
        max_age_days:     verdicts older than this are considered stale
        as_of:            reference time for age check (ISO-8601). Defaults to now().

    Returns:
        {
            "triggered":          [...],   # tickers requiring full analysis
            "confirmed_unchanged":[...],   # tickers to carry forward unchanged
            "trigger_reasons":    {ticker: [reason, ...]},
        }
    """
    now = _parse_ts(as_of) if as_of else datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=max_age_days)

    news_set  = set(t.upper() for t in (news_flagged or []))
    force_set = set(t.upper() for t in (force_tickers or []))

    triggered:   list[str] = []
    unchanged:   list[str] = []
    reasons:     dict[str, list[str]] = {}

    for ticker in tickers:
        t = ticker.upper()
        info = verdicts_with_ts.get(t, {"verdict": "not_reviewed", "run_ts": None})
        verdict = info["verdict"]
        run_ts  = info["run_ts"]

        ticker_reasons: list[str] = []

        # Rule 1: no prior verdict
        if verdict == "not_reviewed" or run_ts is None:
            ticker_reasons.append("no_prior_verdict")

        # Rule 2: news-flagged this run
        if t in news_set:
            ticker_reasons.append(f"news_flagged")

        # Rule 3: stale verdict
        if run_ts and verdict != "not_reviewed":
            verdict_ts = _parse_ts(run_ts)
            if verdict_ts < stale_cutoff:
                age_days = (now - verdict_ts).days
                ticker_reasons.append(f"stale_{age_days}d")

        # Rule 4: force
        if t in force_set:
            ticker_reasons.append("manual_request")

        if ticker_reasons:
            triggered.append(t)
            reasons[t] = ticker_reasons
        else:
            unchanged.append(t)

    return {
        "triggered":           triggered,
        "confirmed_unchanged": unchanged,
        "trigger_reasons":     reasons,
    }


def _parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
