"""
Daily candidates pre-filter — runs locally (free) to select tickers for CCR.

Reads prices.json (all universe tickers) + ledger.db (current portfolio)
→ Outputs daily_candidates.json:
    mandatory : portfolio holdings  (always analyzed, agents need to flag sell)
    opportunity: top N from universe by |technical score|
    total      : ≤ MAX_TOTAL tickers

CCR reads daily_candidates.json in Step 1 instead of the full watchlist.
Token cost stays constant (MAX_TOTAL) regardless of universe size.
"""

from __future__ import annotations
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("USE_SUPABASE", "false")

PRICES_JSON   = Path(__file__).parent.parent / "prices.json"
CANDIDATES_OUT = Path(__file__).parent.parent / "daily_candidates.json"

MAX_TOTAL     = 15   # total tickers sent to CCR per run
MIN_OPPORTUNITY = 5  # always allow at least this many new opportunities


def _quick_score(ind: dict) -> float:
    """
    Fast deterministic technical score from pre-computed indicators.
    Range: -1.0 (bearish) to +1.0 (bullish).
    Mirrors the weighted logic of the Technical Screener agent prompt
    so the pre-filter and the agent agree on what's interesting.
    """
    score = 0.0

    # RSI (weight ~15%)
    rsi = ind.get("rsi_14")
    if rsi is not None:
        if   rsi < 25: score += 0.15
        elif rsi < 35: score += 0.09
        elif rsi < 45: score += 0.04
        elif rsi > 75: score -= 0.15
        elif rsi > 65: score -= 0.09
        elif rsi > 55: score -= 0.04

    # Bollinger Band %B (weight ~20%)
    bb = ind.get("bb_pct_b")
    if bb is not None:
        if   bb < 0.05: score += 0.16    # near/below lower band
        elif bb < 0.25: score += 0.08
        elif bb > 0.95: score -= 0.16    # near/above upper band
        elif bb > 0.75: score -= 0.08

    # Momentum 5d + 1d (weight ~10%)
    p5 = ind.get("pct_change_5d")
    p1 = ind.get("pct_change_1d")
    if p5 is not None and p1 is not None:
        if p5 > 5 and p1 > 0:   score += 0.10
        elif p5 > 2:             score += 0.05
        elif p5 < -5 and p1 < 0: score -= 0.10
        elif p5 < -2:            score -= 0.05

    # MA/EMA trend (weight ~20%)
    close = ind.get("last_close")
    ema20 = ind.get("ema_20")
    ema50 = ind.get("ema_50")
    if close and ema20 and ema50:
        if close > ema20 > ema50:    score += 0.20   # aligned uptrend
        elif close > ema20:          score += 0.10
        elif close < ema20 < ema50:  score -= 0.20   # aligned downtrend
        elif close < ema20:          score -= 0.10

    # Volume (weight ~10%)
    vr = ind.get("volume_ratio")
    p1 = ind.get("pct_change_1d") or 0
    if vr is not None and vr >= 2.0:
        score += 0.10 if p1 > 0 else -0.10
    elif vr is not None and vr >= 1.5:
        score += 0.05 if p1 > 0 else -0.05

    return round(max(-1.0, min(1.0, score)), 4)


def compute_daily_candidates() -> dict:
    """
    Returns the daily_candidates payload:
        mandatory    : portfolio holdings (always analyzed)
        opportunity  : top N from universe by |score|, not already mandatory
        tickers      : combined list (mandatory first)
        scores       : {ticker: score} for all universe tickers
        fetched_at   : timestamp
    """
    # 1. Current portfolio holdings (mandatory)
    from ledger.positions import get_positions
    positions = get_positions()
    mandatory = [p["ticker"] for p in positions]

    # 2. Load universe scores from prices.json
    universe_scores: dict[str, float] = {}
    if PRICES_JSON.exists():
        try:
            data = json.loads(PRICES_JSON.read_text(encoding="utf-8"))
            for ticker, ind in data.get("prices", {}).items():
                if isinstance(ind, dict) and "error" not in ind and ind.get("last_close"):
                    universe_scores[ticker] = _quick_score(ind)
        except Exception as e:
            print(f"Warning: could not read prices.json: {e}")

    # 3. Opportunity pool: exclude mandatory, sort by |score| desc
    mandatory_set = set(mandatory)
    pool = {t: s for t, s in universe_scores.items() if t not in mandatory_set}
    opportunity_budget = max(MIN_OPPORTUNITY, MAX_TOTAL - len(mandatory))
    top_opportunity = sorted(pool.items(), key=lambda x: abs(x[1]), reverse=True)[:opportunity_budget]

    tickers = mandatory + [t for t, _ in top_opportunity]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe_size": len(universe_scores),
        "mandatory_count": len(mandatory),
        "opportunity_count": len(top_opportunity),
        "total": len(tickers),
        "tickers": tickers,
        "mandatory": mandatory,
        "opportunity": [{"ticker": t, "score": s} for t, s in top_opportunity],
        "all_scores": universe_scores,   # full universe scores (for UI/audit)
    }


def run() -> dict:
    result = compute_daily_candidates()
    CANDIDATES_OUT.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8"
    )
    print(
        f"daily_candidates.json: {result['total']} tickers "
        f"(mandatory={result['mandatory_count']}, "
        f"opportunity={result['opportunity_count']}, "
        f"universe={result['universe_size']})"
    )
    if result["mandatory"]:
        print(f"  Mandatory (holdings): {result['mandatory']}")
    print(f"  Opportunity top 5: {result['opportunity'][:5]}")
    return result


if __name__ == "__main__":
    run()
