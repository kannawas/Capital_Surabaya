"""
News fetcher — yfinance .news[] per ticker.

Each article has a publishTime (Unix epoch) which we convert to UTC ISO-8601.
The look-ahead guard can then filter articles by cutoff timestamp.
"""

from __future__ import annotations
from datetime import datetime, timezone

import yfinance as yf

MAX_ARTICLES_PER_TICKER = 10


def fetch_news(tickers: list[str], cutoff_ts: str | None = None) -> dict[str, list[dict]]:
    """
    Fetch recent news for each ticker via yfinance.

    Returns:
        {ticker: [{"ts": str, "title": str, "publisher": str, "url": str}, ...]}

    Articles are pre-filtered to ts <= cutoff_ts (look-ahead safe).
    Sorted newest-first. Capped at MAX_ARTICLES_PER_TICKER.
    """
    cutoff = _parse_cutoff(cutoff_ts)
    result: dict[str, list[dict]] = {}

    for ticker in tickers:
        try:
            raw_news = yf.Ticker(ticker).news or []
            articles = []
            for item in raw_news:
                pub_time = item.get("content", {}).get("pubDate") or item.get("providerPublishTime")
                if pub_time is None:
                    continue

                # yfinance returns either Unix int or ISO string depending on version
                ts = _to_utc_iso(pub_time)
                if ts is None:
                    continue

                art_dt = datetime.fromisoformat(ts)
                if art_dt.tzinfo is None:
                    art_dt = art_dt.replace(tzinfo=timezone.utc)

                if art_dt > cutoff:
                    continue  # look-ahead filter

                title = (
                    item.get("content", {}).get("title")
                    or item.get("title")
                    or ""
                )
                publisher = (
                    item.get("content", {}).get("provider", {}).get("displayName")
                    or item.get("publisher")
                    or ""
                )
                url = (
                    item.get("content", {}).get("canonicalUrl", {}).get("url")
                    or item.get("link")
                    or ""
                )

                articles.append({
                    "ts": ts,
                    "title": title,
                    "publisher": publisher,
                    "url": url,
                    "ticker": ticker,
                })

            articles.sort(key=lambda x: x["ts"], reverse=True)
            result[ticker] = articles[:MAX_ARTICLES_PER_TICKER]

        except Exception:
            result[ticker] = []

    return result


def _parse_cutoff(cutoff_ts: str | None) -> datetime:
    if cutoff_ts is None:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(cutoff_ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _to_utc_iso(pub_time) -> str | None:
    """Convert yfinance publishTime (int epoch or ISO string) to UTC ISO-8601."""
    try:
        if isinstance(pub_time, (int, float)):
            return datetime.fromtimestamp(pub_time, tz=timezone.utc).isoformat()
        if isinstance(pub_time, str):
            dt = datetime.fromisoformat(pub_time.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
    except Exception:
        pass
    return None
