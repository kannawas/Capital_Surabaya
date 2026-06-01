"""
Export a single dashboard.json that the UI fetches.

Aggregates everything the 10 UI pages need from the SQLite ledger:
  - portfolio (NAV, cash, positions)  -> Dashboard, Executor
  - agent outputs (5 agents)          -> Technical/Macro/News/Thesis/Execution pages
  - verdicts                          -> Thesis page
  - limits (recomputed)               -> Risk page
  - runs history                      -> Dashboard

Runs at the end of a CCR routine (SQLite mode), commits dashboard.json to repo.
The GitHub Pages UI fetches it via raw.githubusercontent.com.

Usage:
    python3 routines/export_dashboard.py
"""

from __future__ import annotations
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("USE_SUPABASE", "false")

OUT = Path(__file__).parent.parent / "dashboard.json"


def build_dashboard() -> dict:
    from ledger.storage import select, scalar_sum
    from data.watchlist import get_groups

    # --- portfolio ---
    positions = select("positions", order_by="ticker ASC")
    cash = round(scalar_sum("cash_ledger", "delta"), 2)
    equity = sum(p["shares"] * p["last_price"] for p in positions)
    nav = round(cash + equity, 2)

    # --- latest run ---
    runs = select("runs", order_by="run_id DESC")
    latest_run = runs[0] if runs else None
    latest_run_ts = latest_run["run_ts"] if latest_run else None

    # --- agent outputs (latest run only) ---
    all_outputs = select("agent_outputs", order_by="id DESC")
    agents: dict[str, dict] = {}
    for row in all_outputs:
        name = row["agent_name"]
        if name not in agents:  # keep newest only
            try:
                agents[name] = json.loads(row["output_json"])
            except Exception:
                agents[name] = {"_parse_error": True}

    # --- verdicts ---
    verdicts = select("verdict_store", order_by="ticker ASC")

    # --- orders (latest run) ---
    orders = []
    if latest_run:
        orders = select("orders", where={"run_id": latest_run["run_id"]})

    # --- nav history ---
    nav_hist = select("nav_history", order_by="nav_id ASC")

    # --- recompute limits for Risk page (from macro + verdicts) ---
    limits_summary = _recompute_limits(agents, verdicts, get_groups())

    # --- per-position breakdown with weights ---
    pos_breakdown = []
    for p in positions:
        val = round(p["shares"] * p["last_price"], 2)
        pos_breakdown.append({
            "ticker": p["ticker"],
            "shares": p["shares"],
            "avg_cost": p["avg_cost"],
            "last_price": p["last_price"],
            "value": val,
            "weight_pct": round(val / nav * 100, 2) if nav else 0,
            "unrealized_pnl": round(val - p["shares"] * p["avg_cost"], 2),
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "latest_run_ts": latest_run_ts,
        "portfolio": {
            "nav": nav,
            "cash": cash,
            "equity": round(equity, 2),
            "cash_pct": round(cash / nav * 100, 2) if nav else 100,
            "positions": pos_breakdown,
            "position_count": len(positions),
        },
        "agents": agents,
        "verdicts": [{"ticker": v["ticker"], "verdict": v["verdict"]} for v in verdicts],
        "orders": orders,
        "limits": limits_summary,
        "nav_history": [{"ts": h["ts"], "nav": h["nav"]} for h in nav_hist],
        "runs_count": len(runs),
    }


def _recompute_limits(agents: dict, verdicts: list, groups: dict) -> dict:
    """Recompute deterministic limits from the latest macro + verdicts for the Risk page."""
    try:
        from core.limits import LimitInputs, compute_limits
        macro = agents.get("macro_intelligence", {})
        regime = macro.get("regime", "neutral")
        event_lock = bool(macro.get("event_lock", False))

        news = agents.get("news_reporter", {})
        news_flagged = news.get("news_flagged", [])

        verdict_map = {v["ticker"]: v["verdict"] for v in verdicts}

        lim = compute_limits(LimitInputs(
            regime=regime,
            event_lock=event_lock,
            thesis_verdicts=verdict_map,
            convictions={},
            groups=groups,
            news_flagged=news_flagged,
        ))
        return {
            "regime": lim.regime,
            "regime_factor": lim.regime_factor,
            "cash_floor_pct": round(lim.cash_floor * 100, 2),
            "gross_exposure_cap_pct": round(lim.gross_exposure_cap * 100, 2),
            "event_lock": lim.event_lock,
            "news_addon_pct": round(lim.news_addon * 100, 2),
            "group_caps": {g: round(c * 100, 2) for g, c in lim.group_caps.items()},
            "ticker_caps": {
                t: {
                    "single_name_cap_pct": round(tl.single_name_cap * 100, 2),
                    "thesis_verdict": tl.thesis_verdict,
                }
                for t, tl in lim.ticker_limits.items()
            },
        }
    except Exception as e:
        return {"_error": str(e)}


if __name__ == "__main__":
    data = build_dashboard()
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    p = data["portfolio"]
    print(f"dashboard.json written: NAV ${p['nav']:,.2f} | cash ${p['cash']:,.2f} | "
          f"{p['position_count']} positions | {len(data['agents'])} agents | "
          f"{len(data['verdicts'])} verdicts")
