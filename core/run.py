"""
Run loop — Phase 1 entry point.

Connects: limits → sizing → ledger → NAV snapshot.
In Phase 1: accepts hand-typed intents (no LLM).
In Phase 2: intents will come from Execution agent output.

Usage (Phase 1 hand-typed):
    python -m core.run --intents '[{"ticker":"MSFT","side":"buy","conviction":0.7,"limit_price":427}]'
"""

from __future__ import annotations
import json
from dataclasses import asdict
from datetime import datetime, timezone

from core.limits import LimitInputs, compute_limits
from core.sizing import IntentInput, compute_order
from core.state import load_all_convictions, load_all_verdicts
from data.macro import fetch_macro
from data.watchlist import get_active, get_groups
from ledger.storage import insert, init_schema
from ledger.nav import compute_nav
from ledger.orders import record_order
from ledger.positions import get_cash, get_positions
from data.prices import fetch_latest_price


def run(
    intents: list[dict],
    cutoff_ts: str | None = None,
    news_flagged: list[str] | None = None,
    macro_override: dict | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Execute one run of the deterministic core.

    Args:
        intents:        list of intent dicts (ticker, side, conviction, limit_price)
        cutoff_ts:      decision cutoff (ISO-8601). Defaults to now().
        news_flagged:   tickers flagged as material by News agent.
        macro_override: Macro Intelligence Agent output dict. If provided, its
                        regime and event_lock override the mechanical data/macro.py values.
                        This is the only path by which LLM output enters the deterministic core.
        dry_run:        if True, compute orders but don't write to ledger.

    Returns:
        run_report dict with limits, order_plans, fills, nav_after.
    """
    init_schema()
    ts = cutoff_ts or datetime.now(timezone.utc).isoformat()
    news_flagged = news_flagged or []

    # --- current state ---
    positions = get_positions()
    cash = get_cash()
    tickers = get_active()
    groups = get_groups()

    # compute NAV before (no snapshot save)
    nav_before = compute_nav(update_prices=True, save_snapshot=False)
    nav = nav_before["nav"]

    # --- load persisted verdicts + convictions ---
    verdicts = load_all_verdicts(tickers)
    stored_convictions = load_all_convictions(tickers)

    # override with intents' convictions (fresher)
    intent_convictions: dict[str, float] = {}
    for intent_dict in intents:
        t = intent_dict["ticker"].upper()
        if "conviction" in intent_dict:
            intent_convictions[t] = intent_dict["conviction"]

    merged_convictions: dict[str, float | None] = {**stored_convictions, **intent_convictions}
    # filter out None (not_reviewed) for LimitInputs — only float values
    float_convictions = {t: v for t, v in merged_convictions.items() if v is not None}

    # --- macro: mechanical fetch, then apply agent override if provided ---
    macro = fetch_macro(ts)

    if macro_override:
        # Macro Intelligence Agent output takes precedence over mechanical values.
        # Only regime and event_lock flow into the deterministic core.
        agent_regime     = macro_override.get("regime")
        agent_event_lock = macro_override.get("event_lock")
        valid_regimes    = {"risk_on", "neutral", "risk_off"}
        if agent_regime in valid_regimes:
            macro["regime"]     = agent_regime
        if isinstance(agent_event_lock, bool):
            macro["event_lock"] = agent_event_lock

    # --- compute limits ---
    limit_inputs = LimitInputs(
        regime=macro["regime"],
        event_lock=macro["event_lock"],
        thesis_verdicts=verdicts,
        convictions=float_convictions,
        groups=groups,
        news_flagged=news_flagged,
    )
    limits = compute_limits(limit_inputs)

    # held shares lookup
    held: dict[str, int] = {p["ticker"]: p["shares"] for p in positions}

    # --- size each intent ---
    order_plans = []
    for intent_dict in intents:
        ticker = intent_dict["ticker"].upper()
        conviction = intent_dict.get("conviction", merged_convictions.get(ticker))
        if conviction is None:
            conviction = 0.0

        last_price = fetch_latest_price(ticker) or intent_dict.get("limit_price", 1.0)

        intent = IntentInput(
            ticker=ticker,
            side=intent_dict["side"],
            conviction=float(conviction),
            limit_price=intent_dict.get("limit_price", last_price),
            group=groups.get(ticker),
        )

        plan = compute_order(
            intent=intent,
            nav=nav,
            last_price=last_price,
            limits=limits,
            held_shares=held.get(ticker, 0),
        )
        order_plans.append(plan)

    if dry_run:
        return {
            "dry_run": True,
            "ts": ts,
            "nav_before": nav,
            "macro": macro,
            "limits": _limits_summary(limits),
            "order_plans": [asdict(p) for p in order_plans],
        }

    # --- create run record ---
    run_id = insert("runs", {"run_ts": ts, "nav_before": nav, "cash_before": cash})

    # --- execute fills ---
    fills = []
    for plan in order_plans:
        if plan.side == "skip":
            fills.append({"ticker": plan.ticker, "status": "skipped", "reason": plan.skipped_reason})
            continue

        last_price = fetch_latest_price(plan.ticker) or plan.limit_price

        result = record_order(
            run_id=run_id,
            ticker=plan.ticker,
            side=plan.side,
            intended_pct=plan.size_pct,
            limit_price=plan.limit_price,
            shares=plan.shares,
            open_price=last_price,
            ts=ts,
            is_market=plan.is_market,
        )
        fills.append({
            "ticker": plan.ticker,
            "side": plan.side,
            "shares": plan.shares,
            "status": result.status,
            "fill_price": result.fill_price,
            "cash_delta": result.cash_delta,
        })

    # --- NAV snapshot at end of run ---
    nav_after = compute_nav(update_prices=False, save_snapshot=True)

    return {
        "run_id": run_id,
        "ts": ts,
        "nav_before": nav,
        "nav_after": nav_after["nav"],
        "macro": {
            "regime": macro["regime"],
            "event_lock": macro["event_lock"],
            "vix": (macro.get("vix") or {}).get("value"),
        },
        "limits": _limits_summary(limits),
        "order_plans": [asdict(p) for p in order_plans],
        "fills": fills,
    }


def _limits_summary(limits) -> dict:
    return {
        "regime": limits.regime,
        "regime_factor": limits.regime_factor,
        "cash_floor_pct": limits.cash_floor,
        "gross_exposure_cap_pct": limits.gross_exposure_cap,
        "event_lock": limits.event_lock,
        "news_addon": limits.news_addon,
        "group_caps": limits.group_caps,
        "ticker_caps": {
            t: {
                "single_name_cap": tl.single_name_cap,
                "thesis_verdict": tl.thesis_verdict,
                "conviction": tl.conviction,
            }
            for t, tl in limits.ticker_limits.items()
        },
    }


if __name__ == "__main__":
    import argparse, sys
    parser = argparse.ArgumentParser()
    parser.add_argument("--intents", type=str, required=True, help="JSON array of intents")
    parser.add_argument("--cutoff", type=str, default=None)
    parser.add_argument("--news-flagged", type=str, default="[]")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    report = run(
        intents=json.loads(args.intents),
        cutoff_ts=args.cutoff,
        news_flagged=json.loads(args.news_flagged),
        dry_run=args.dry_run,
    )
    print(json.dumps(report, indent=2))
