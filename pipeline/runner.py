"""
Pipeline orchestrator — runs one full cycle of all 5 agents then posts to ledger.

Run cadence (ICT = UTC+7):
  post_close  : 04:00 ICT  (after US market close)
  pre_market  : 15:00 ICT  (before US market open)

Usage:
  python -m pipeline.runner --run-type post_close
  python -m pipeline.runner --run-type pre_market
  python -m pipeline.runner --run-type post_close --dry-run
"""

from __future__ import annotations
import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from data.packets import build_packet, inject_research_outputs
from data.watchlist import get_active, sync_from_file, get_groups
from ledger.db import init_db
from ledger.guard import make_cutoff

from pipeline.agent_caller import call_agent, call_execution_agent
from pipeline.parser import (
    parse_technical, parse_macro, parse_news,
    parse_thesis, parse_execution, parse_watchlist_removals,
)
from pipeline.trigger import compute_triggered_tickers
from core.state import save_thesis_verdict, load_all_verdicts, load_all_verdicts_with_ts

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "pipeline.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("pipeline")

# Agents that run in parallel after news completes
PARALLEL_AGENTS = ["technical_screener", "macro_intelligence", "fundamental_thesis"]


def _run_research_agents(
    tickers: list[str],
    cutoff_ts: str,
) -> tuple[dict[str, dict], dict[str, str]]:
    """
    Call the 4 research agents in two stages:

    Stage 1 — News Reporter (sequential, ~10s):
        news_flagged is needed before we can compute triggered_tickers
        for the Fundamental Thesis packet.

    Stage 2 — Technical + Macro + Thesis (parallel):
        Thesis packet now contains triggered_tickers and prior_verdicts,
        so the agent only deep-analyzes the tickers that need it.

    Returns ({agent_name: parsed_output}, {agent_name: error_str}).
    """
    results: dict[str, dict] = {}
    errors:  dict[str, str]  = {}

    # ── Stage 1: News Reporter ────────────────────────────────────────────────
    log.info("Stage 1: calling news_reporter...")
    news_packet = build_packet("news_reporter", cutoff_ts, tickers)
    try:
        news_raw           = call_agent("news_reporter", news_packet)
        results["news_reporter"] = parse_news(news_raw)
        news_flagged       = results["news_reporter"].get("news_flagged", [])
        log.info(f"news_reporter done — flagged: {news_flagged}")
    except Exception as e:
        log.error(f"news_reporter failed: {e}")
        errors["news_reporter"] = str(e)
        news_flagged = []

    # ── Compute triggered_tickers ─────────────────────────────────────────────
    verdicts_with_ts = load_all_verdicts_with_ts(tickers)
    trigger_result   = compute_triggered_tickers(
        tickers          = tickers,
        verdicts_with_ts = verdicts_with_ts,
        news_flagged     = news_flagged,
        as_of            = cutoff_ts,
    )
    triggered    = trigger_result["triggered"]
    unchanged    = trigger_result["confirmed_unchanged"]
    reasons      = trigger_result["trigger_reasons"]

    log.info(
        f"Triggered tickers ({len(triggered)}): {triggered} | "
        f"Unchanged ({len(unchanged)}): {unchanged}"
    )

    # ── Stage 2: Build packets for remaining 3 agents ────────────────────────
    packets = {
        "technical_screener": build_packet("technical_screener", cutoff_ts, tickers),
        "macro_intelligence":  build_packet("macro_intelligence",  cutoff_ts, tickers),
        "fundamental_thesis":  build_packet("fundamental_thesis",  cutoff_ts, tickers),
    }

    # Enrich thesis packet with triggered_tickers + prior_verdicts
    packets["fundamental_thesis"].update({
        "triggered_tickers":    triggered,
        "confirmed_unchanged":  unchanged,
        "trigger_reasons":      reasons,
        "prior_verdicts": {
            t: {
                "verdict":        info["verdict"],
                "verdict_ts":     info["run_ts"] or "",
                "reason_summary": "",
            }
            for t, info in verdicts_with_ts.items()
        },
    })

    parsers = {
        "technical_screener": parse_technical,
        "macro_intelligence":  parse_macro,
        "fundamental_thesis":  parse_thesis,
    }

    def _call_one(agent: str) -> tuple[str, dict | None, str | None]:
        try:
            log.info(f"Calling {agent}...")
            raw    = call_agent(agent, packets[agent])
            parsed = parsers[agent](raw)
            log.info(f"{agent} done.")
            return agent, parsed, None
        except Exception as e:
            log.error(f"{agent} failed: {e}")
            return agent, None, str(e)

    # ── Stage 2 parallel ──────────────────────────────────────────────────────
    log.info("Stage 2: calling technical_screener, macro_intelligence, fundamental_thesis in parallel...")
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(_call_one, a): a for a in PARALLEL_AGENTS}
        for future in as_completed(futures):
            agent, parsed, err = future.result()
            if parsed is not None:
                results[agent] = parsed
            else:
                errors[agent] = err

    if errors:
        log.warning(f"Research agent errors: {errors}")

    return results, errors


def _persist_thesis_verdicts(thesis_output: dict, run_ts: str) -> None:
    """Save non-confirmed verdicts to verdict_store."""
    verdict_map = thesis_output.get("verdict_map", {})
    valid = {"intact", "weakening", "watch", "broken"}
    for ticker, verdict in verdict_map.items():
        if verdict in valid:
            save_thesis_verdict(ticker, verdict, run_ts=run_ts, source="fundamental_agent")
            log.info(f"Verdict saved: {ticker} = {verdict}")


def _apply_watchlist_removals(removals: list[dict]) -> None:
    """Apply watchlist removals with 7-day cooldown."""
    if not removals:
        return
    from datetime import timedelta
    from ledger.db import get_conn
    from ledger.db import init_db
    cooldown_until = (
        datetime.now(timezone.utc) + timedelta(days=7)
    ).isoformat()

    with get_conn() as conn:
        for r in removals:
            ticker = r.get("ticker", "").upper()
            reason = r.get("reason", "")
            if not ticker:
                continue
            conn.execute(
                """UPDATE watchlist
                   SET active=0, cooldown_until=?, note=?
                   WHERE ticker=?""",
                (cooldown_until, f"Removed by Execution: {reason}", ticker),
            )
            log.info(f"Watchlist removal: {ticker} — {reason} (cooldown until {cooldown_until[:10]})")


def run_pipeline(
    run_type: str = "post_close",
    dry_run: bool = False,
    cutoff_ts: str | None = None,
) -> dict:
    """
    Full pipeline: 4 research agents → Execution → core/run.py → ledger.

    Args:
        run_type:   "post_close" or "pre_market"
        dry_run:    compute everything but don't write to ledger
        cutoff_ts:  override decision cutoff (ISO-8601). Defaults to now().

    Returns:
        pipeline_report dict.
    """
    init_db()
    sync_from_file()                          # sync watchlist.json → SQLite

    cutoff = make_cutoff(cutoff_ts)
    cutoff_str = cutoff.isoformat()
    run_ts = datetime.now(timezone.utc).isoformat()

    log.info(f"=== Pipeline START {run_type} | cutoff={cutoff_str} | dry_run={dry_run} ===")

    tickers = get_active()
    if not tickers:
        log.error("No active tickers in watchlist. Aborting.")
        return {"error": "empty_watchlist"}

    # ── Step 1-3: Research agents (parallel) ─────────────────────────────────
    research_outputs, research_errors = _run_research_agents(tickers, cutoff_str)

    # ── Step 4: Persist thesis verdicts ──────────────────────────────────────
    if "fundamental_thesis" in research_outputs:
        _persist_thesis_verdicts(research_outputs["fundamental_thesis"], run_ts)

    # ── Step 5: Build execution packet ───────────────────────────────────────
    exec_packet = build_packet("execution", cutoff_str, tickers)

    # Inject research outputs (blindness gate enforced in inject_research_outputs)
    # Reshape to match the format execution agent expects
    safe_outputs = {}
    if "technical_screener" in research_outputs:
        safe_outputs["technical_screener"] = research_outputs["technical_screener"]
    if "macro_intelligence" in research_outputs:
        safe_outputs["macro_intelligence"] = research_outputs["macro_intelligence"]
    if "news_reporter" in research_outputs:
        safe_outputs["news_reporter"] = research_outputs["news_reporter"]
    if "fundamental_thesis" in research_outputs:
        safe_outputs["fundamental_thesis"] = research_outputs["fundamental_thesis"]

    from data.packets import inject_research_outputs
    exec_packet = inject_research_outputs(exec_packet, safe_outputs)

    # ── Step 6: Execution agent ───────────────────────────────────────────────
    exec_intents = []
    watchlist_removals = []
    exec_error = None

    try:
        log.info("Calling execution agent...")
        exec_raw = call_execution_agent(exec_packet)
        exec_intents = parse_execution(exec_raw)
        watchlist_removals = parse_watchlist_removals(exec_raw)
        log.info(f"Execution done: {len(exec_intents)} intents")
    except Exception as e:
        exec_error = str(e)
        log.error(f"Execution agent failed: {e}")

    if not exec_intents:
        log.warning("No intents from Execution Agent — aborting ledger writes.")
        return {
            "run_type": run_type,
            "cutoff_ts": cutoff_str,
            "research_errors": research_errors,
            "exec_error": exec_error,
            "aborted": True,
        }

    # ── Step 7: Apply watchlist removals ─────────────────────────────────────
    if not dry_run:
        _apply_watchlist_removals(watchlist_removals)

    # ── Step 8: Feed intents → core/run.py ───────────────────────────────────

    # Extract macro override from agent output (may differ from mechanical)
    macro_out = research_outputs.get("macro_intelligence", {})
    news_out  = research_outputs.get("news_reporter", {})
    news_flagged = news_out.get("news_flagged", [])

    from core.run import run as core_run
    run_report = core_run(
        intents=exec_intents,
        cutoff_ts=cutoff_str,
        news_flagged=news_flagged,
        macro_override=macro_out,         # agent may override mechanical regime
        dry_run=dry_run,
    )

    log.info(
        f"=== Pipeline DONE | nav_before={run_report.get('nav_before')} "
        f"nav_after={run_report.get('nav_after')} "
        f"fills={len([f for f in run_report.get('fills', []) if f.get('status')=='filled'])} ==="
    )

    return {
        "run_type":           run_type,
        "cutoff_ts":          cutoff_str,
        "tickers":            tickers,
        "research_errors":    research_errors,
        "exec_error":         exec_error,
        "watchlist_removals": watchlist_removals,
        "core_run":           run_report,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Capital Surabaya pipeline runner")
    parser.add_argument(
        "--run-type",
        choices=["post_close", "pre_market"],
        default="post_close",
        help="Which run to execute (default: post_close)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute everything but don't write to ledger",
    )
    parser.add_argument(
        "--cutoff",
        type=str,
        default=None,
        help="Override cutoff timestamp (ISO-8601 UTC)",
    )
    args = parser.parse_args()

    report = run_pipeline(
        run_type=args.run_type,
        dry_run=args.dry_run,
        cutoff_ts=args.cutoff,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
