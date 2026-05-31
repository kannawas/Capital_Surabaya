"""Persistent conviction + thesis verdict store — works in SQLite and Supabase mode."""

from __future__ import annotations
from datetime import datetime, timezone
from ledger.storage import insert, select, upsert


def save_conviction(ticker: str, conviction: float,
                    run_ts: str | None = None, source: str = "execution_agent") -> None:
    ts = run_ts or datetime.now(timezone.utc).isoformat()
    upsert("conviction_store", {
        "ticker": ticker.upper(), "conviction": conviction,
        "status": "reviewed", "run_ts": ts, "source": source,
    }, conflict_col="ticker")


def get_conviction(ticker: str) -> tuple[float | None, str]:
    rows = select("conviction_store", where={"ticker": ticker.upper()})
    if not rows:
        return None, "not_reviewed"
    return rows[0]["conviction"], rows[0]["status"]


def save_thesis_verdict(ticker: str, verdict: str,
                        run_ts: str | None = None, source: str = "fundamental_agent") -> None:
    valid = {"intact", "weakening", "watch", "broken"}
    if verdict not in valid:
        raise ValueError(f"Invalid verdict {verdict!r}. Must be one of {valid}")
    ts = run_ts or datetime.now(timezone.utc).isoformat()
    upsert("verdict_store", {
        "ticker": ticker.upper(), "verdict": verdict,
        "run_ts": ts, "source": source,
    }, conflict_col="ticker")


def get_thesis_verdict(ticker: str) -> str:
    rows = select("verdict_store", where={"ticker": ticker.upper()})
    return rows[0]["verdict"] if rows else "not_reviewed"


def load_all_convictions(tickers: list[str]) -> dict[str, float | None]:
    return {t: get_conviction(t)[0] for t in tickers}


def load_all_verdicts(tickers: list[str]) -> dict[str, str]:
    return {t: get_thesis_verdict(t) for t in tickers}


def load_all_verdicts_with_ts(tickers: list[str]) -> dict[str, dict]:
    result = {}
    for ticker in tickers:
        rows = select("verdict_store", where={"ticker": ticker.upper()})
        if rows:
            result[ticker] = {"verdict": rows[0]["verdict"], "run_ts": rows[0]["run_ts"]}
        else:
            result[ticker] = {"verdict": "not_reviewed", "run_ts": None}
    return result
