"""
Test case from PROJECT_SPEC.md §8 — authoritative orders, NAV $304,200.

MSFT  buy  21 sh  @ ~427   → filled
COST  buy   8 sh  @ ~951   → filled
AVGO  buy  15 sh  @ ~239   → MISSED (gapped above limit)
MRVL  sell 76 sh  @ ~72    → filled (sell_or_avoid resolution)

Cash before: $111,600  →  after: $100,500 (approx)
"""

import sys
from pathlib import Path

# Allow running from project root without install
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
import tempfile
import sqlite3
from datetime import datetime, timezone

# Point DB to a temp file so tests don't corrupt the real ledger
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()

import ledger.db as _db_module
_db_module.DB_PATH = Path(_tmp.name)

from ledger.db import init_db
from ledger.orders import record_order
from ledger.positions import get_cash, get_positions, seed_cash


RUN_TS = "2026-05-09T09:30:00+00:00"
NAV = 304_200.0
CASH_BEFORE = 111_600.0


def setup_run() -> int:
    init_db()
    seed_cash(CASH_BEFORE, reason="injection", ts="2026-05-09T00:00:00+00:00")

    from ledger.db import get_conn
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO runs (run_ts, nav_before, cash_before) VALUES (?,?,?)",
            (RUN_TS, NAV, CASH_BEFORE),
        )
        return cur.lastrowid


def test_spec8():
    run_id = setup_run()
    ts = RUN_TS

    # --- MSFT buy 21 @ 427 (open ~ limit, fills) ---
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

    # --- AVGO buy 15 @ 239 — open > limit → MISSED ---
    avgo = record_order(run_id, "AVGO", "buy",
                        intended_pct=0.0118, limit_price=239.0,
                        shares=15, open_price=245.0,  # gapped above limit
                        ts=ts)
    assert avgo.status == "missed", f"AVGO should be MISSED, got {avgo.status}"
    assert avgo.cash_delta == 0.0

    # --- Pre-seed MRVL position (76 shares held) ---
    from ledger.db import get_conn
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO positions (ticker, shares, avg_cost, last_price, updated_ts) VALUES (?,?,?,?,?)",
            ("MRVL", 76, 68.0, 72.0, ts),
        )

    # --- MRVL sell 76 @ 72 (sell_or_avoid resolution: held → sell) ---
    mrvl = record_order(run_id, "MRVL", "sell",
                        intended_pct=0.0, limit_price=72.0,
                        shares=76, open_price=72.0, ts=ts)
    assert mrvl.status == "filled", f"MRVL should fill, got {mrvl.status}"
    assert mrvl.shares == 76
    assert mrvl.cash_delta > 0  # cash inflow from sell

    # --- Verify cash after ---
    cash_after = get_cash()
    # MSFT cost ≈ 427 × 21 = 8,967
    # COST cost ≈ 951 × 8  = 7,608
    # MRVL proceeds ≈ 72 × 76 = 5,472
    # Net: 111,600 - 8,967 - 7,608 + 5,472 ≈ 100,497
    # (slippage adjusts slightly)
    assert 99_000 < cash_after < 102_000, f"Cash after should be ~$100,500, got {cash_after:,.2f}"

    # --- Verify positions ---
    positions = get_positions()
    tickers = {p["ticker"]: p for p in positions}
    assert "MSFT" in tickers, "MSFT position missing"
    assert tickers["MSFT"]["shares"] == 21
    assert "COST" in tickers, "COST position missing"
    assert tickers["COST"]["shares"] == 8
    assert "AVGO" not in tickers, "AVGO should not be in positions (missed)"
    assert "MRVL" not in tickers, "MRVL should be gone (fully sold)"

    # --- Verify filled count ---
    with get_conn() as conn:
        filled = conn.execute(
            "SELECT COUNT(*) as n FROM orders WHERE run_id=? AND status='filled'", (run_id,)
        ).fetchone()["n"]
        missed = conn.execute(
            "SELECT COUNT(*) as n FROM orders WHERE run_id=? AND status='missed'", (run_id,)
        ).fetchone()["n"]

    assert filled == 3, f"Expected 3 filled, got {filled}"
    assert missed == 1, f"Expected 1 missed (AVGO), got {missed}"

    print("\n=== SPEC §8 TEST PASSED ===")
    print(f"  Cash before : ${CASH_BEFORE:>12,.2f}")
    print(f"  Cash after  : ${cash_after:>12,.2f}")
    print(f"  Net change  : ${cash_after - CASH_BEFORE:>+12,.2f}")
    print(f"  Filled: {filled}  Missed: {missed}")
    print(f"  Positions: {list(tickers.keys())}")

    msft_pct = (tickers['MSFT']['shares'] * tickers['MSFT']['last_price']) / NAV * 100
    cost_pct = (tickers['COST']['shares'] * tickers['COST']['last_price']) / NAV * 100
    print(f"  MSFT {tickers['MSFT']['shares']} sh @ {tickers['MSFT']['last_price']:.2f} = {msft_pct:.2f}% NAV")
    print(f"  COST {tickers['COST']['shares']} sh @ {tickers['COST']['last_price']:.2f} = {cost_pct:.2f}% NAV")


if __name__ == "__main__":
    test_spec8()
    print("\nAll assertions passed.")
