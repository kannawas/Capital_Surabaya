"""
Test case from PROJECT_SPEC.md §8 — authoritative orders, NAV $304,200.

MSFT  buy  21 sh  @ ~427   -> filled
COST  buy   8 sh  @ ~951   -> filled
AVGO  buy  15 sh  @ ~239   -> MISSED (gapped above limit)
MRVL  sell 76 sh  @ ~72    -> filled (sell_or_avoid resolution)

Cash before: $111,600  ->  after: ~$100,500
"""

import sys
import os
from pathlib import Path

# Force SQLite mode BEFORE importing any ledger code
os.environ["USE_SUPABASE"] = "false"
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_KEY", None)

sys.path.insert(0, str(Path(__file__).parent.parent))

import tempfile
from datetime import datetime, timezone

# Point storage to a temp DB so tests don't touch the real ledger
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()

import ledger.storage as _storage
_storage.DB_PATH = Path(_tmp.name)

from ledger.storage import init_schema, insert, upsert, select
from ledger.orders import record_order
from ledger.positions import get_cash, get_positions, seed_cash


RUN_TS = "2026-05-09T09:30:00+00:00"
NAV = 304_200.0
CASH_BEFORE = 111_600.0


def setup_run() -> int:
    init_schema()
    seed_cash(CASH_BEFORE, reason="injection", ts="2026-05-09T00:00:00+00:00")
    return insert("runs", {"run_ts": RUN_TS, "nav_before": NAV, "cash_before": CASH_BEFORE})


def test_spec8():
    run_id = setup_run()
    ts = RUN_TS

    # --- MSFT buy 21 @ 427 (open == limit, fills) ---
    msft = record_order(run_id, "MSFT", "buy",
                        intended_pct=0.0295, limit_price=427.0,
                        shares=21, open_price=427.0, ts=ts)
    assert msft.status == "filled", f"MSFT should fill, got {msft.status}"
    assert msft.shares == 21

    # --- COST buy 8 @ 951 ---
    cost = record_order(run_id, "COST", "buy",
                        intended_pct=0.0250, limit_price=951.0,
                        shares=8, open_price=951.0, ts=ts)
    assert cost.status == "filled", f"COST should fill, got {cost.status}"
    assert cost.shares == 8

    # --- AVGO buy 15 @ 239 -> open > limit -> MISSED ---
    avgo = record_order(run_id, "AVGO", "buy",
                        intended_pct=0.0118, limit_price=239.0,
                        shares=15, open_price=245.0,  # gapped above limit
                        ts=ts)
    assert avgo.status == "missed", f"AVGO should be MISSED, got {avgo.status}"
    assert avgo.cash_delta == 0.0

    # --- Pre-seed MRVL position (76 shares held) ---
    upsert("positions", {
        "ticker": "MRVL", "shares": 76, "avg_cost": 68.0,
        "last_price": 72.0, "updated_ts": ts,
    }, conflict_col="ticker")

    # --- MRVL sell 76 @ 72 (sell_or_avoid resolution: held -> sell) ---
    mrvl = record_order(run_id, "MRVL", "sell",
                        intended_pct=0.0, limit_price=72.0,
                        shares=76, open_price=72.0, ts=ts, is_market=True)
    assert mrvl.status == "filled", f"MRVL should fill, got {mrvl.status}"
    assert mrvl.shares == 76
    assert mrvl.cash_delta > 0  # cash inflow from sell

    # --- Verify cash after ---
    cash_after = get_cash()
    assert 99_000 < cash_after < 102_000, f"Cash after should be ~$100,500, got {cash_after:,.2f}"

    # --- Verify positions ---
    positions = get_positions()
    tickers = {p["ticker"]: p for p in positions}
    assert tickers["MSFT"]["shares"] == 21, "MSFT position wrong"
    assert tickers["COST"]["shares"] == 8, "COST position wrong"
    assert "AVGO" not in tickers, "AVGO should not be in positions (missed)"
    assert "MRVL" not in tickers, "MRVL should be gone (fully sold)"

    # --- Verify filled / missed counts ---
    filled = len(select("orders", where={"run_id": run_id, "status": "filled"}))
    missed = len(select("orders", where={"run_id": run_id, "status": "missed"}))
    assert filled == 3, f"Expected 3 filled, got {filled}"
    assert missed == 1, f"Expected 1 missed (AVGO), got {missed}"

    print("\n=== SPEC 8 TEST PASSED ===")
    print(f"  Cash before : ${CASH_BEFORE:>12,.2f}")
    print(f"  Cash after  : ${cash_after:>12,.2f}")
    print(f"  Net change  : ${cash_after - CASH_BEFORE:>+12,.2f}")
    print(f"  Filled: {filled}  Missed: {missed}")
    print(f"  Positions: {list(tickers.keys())}")


if __name__ == "__main__":
    test_spec8()
    print("\nAll assertions passed.")
    try:
        os.unlink(_tmp.name)
    except Exception:
        pass
