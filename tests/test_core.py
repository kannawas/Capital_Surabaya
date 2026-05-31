"""
Tests for Deterministic Core — spec §4 formulas.

Verifies: limit formulas, clamp bounds, sizing, sell_or_avoid, event lock,
          not_reviewed fallbacks, conviction carry-forward.
"""

import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Force SQLite mode BEFORE importing any ledger code
os.environ["USE_SUPABASE"] = "false"
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_KEY", None)

import ledger.storage as _db
tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp.close()
_db.DB_PATH = Path(tmp.name)

from ledger.storage import init_schema as init_db
init_db()

from core.limits import LimitInputs, compute_limits, REGIME_FACTOR, VERDICT_FACTOR
from core.sizing import IntentInput, compute_order
from core.state import save_conviction, get_conviction, save_thesis_verdict, get_thesis_verdict


# ── helpers ──────────────────────────────────────────────────────────────────

def make_limits(
    regime="neutral",
    event_lock=False,
    verdicts=None,
    convictions=None,
    groups=None,
    news_flagged=None,
):
    return compute_limits(LimitInputs(
        regime=regime,
        event_lock=event_lock,
        thesis_verdicts=verdicts or {},
        convictions=convictions or {},
        groups=groups or {},
        news_flagged=news_flagged or [],
    ))


# ── limit formula tests ───────────────────────────────────────────────────────

def test_regime_factors():
    for regime, expected in REGIME_FACTOR.items():
        lim = make_limits(regime=regime)
        assert lim.regime_factor == expected, f"{regime} factor wrong"
    print("PASS: regime factors")


def test_cash_floor_neutral():
    # base 10% × (2 - 0.90) = 10% × 1.10 = 11%  → clamp [10%, 25%] → 11%
    lim = make_limits(regime="neutral")
    assert abs(lim.cash_floor - 0.11) < 1e-6, f"cash_floor={lim.cash_floor}"
    print(f"PASS: cash_floor neutral={lim.cash_floor:.4f}")


def test_cash_floor_news_addon():
    # 2 tickers flagged → +4% news addon → 11% + 4% = 15%
    lim = make_limits(regime="neutral", news_flagged=["NVDA", "MSFT"])
    assert abs(lim.cash_floor - 0.15) < 1e-6, f"cash_floor={lim.cash_floor}"
    print(f"PASS: cash_floor with news addon={lim.cash_floor:.4f}")


def test_cash_floor_clamp_max():
    # 10 tickers flagged → addon = capped at 10% → 11% + 10% = 21% < 25% max
    lim = make_limits(regime="neutral", news_flagged=[f"T{i}" for i in range(10)])
    assert lim.cash_floor <= 0.25
    print(f"PASS: cash_floor clamp max={lim.cash_floor:.4f}")


def test_gross_exposure_neutral():
    # base 85% × 0.90 × 1 = 76.5% → clamp [50%, 90%] → 76.5%
    lim = make_limits(regime="neutral")
    assert abs(lim.gross_exposure_cap - 0.765) < 1e-6, f"gross={lim.gross_exposure_cap}"
    print(f"PASS: gross_exposure neutral={lim.gross_exposure_cap:.4f}")


def test_event_lock_kills_exposure():
    # event_lock → gross = 85% × regime × 0 = 0 → clamp → 50%
    lim = make_limits(regime="risk_on", event_lock=True)
    assert lim.gross_exposure_cap == 0.50
    assert lim.event_lock is True
    print(f"PASS: event_lock clamps gross to 50%")


def test_single_name_cap_spec_example():
    # spec example: AVGO → ~5.3%
    # neutral(0.90) × intact(1.0) × conviction(0.73) × 8% = 5.256% → clamp [5%,12%] → 5.256%
    lim = make_limits(
        regime="neutral",
        verdicts={"AVGO": "intact"},
        convictions={"AVGO": 0.73},
    )
    cap = lim.ticker_limits["AVGO"].single_name_cap
    assert 0.05 <= cap <= 0.12, f"cap={cap}"
    assert abs(cap - 0.052488) < 0.001, f"AVGO cap={cap:.6f} expected ~5.25%"
    print(f"PASS: AVGO single_name_cap={cap:.4f} (~5.25% per spec)")


def test_single_name_cap_clamp_min():
    # broken thesis + low conviction → raw very small → clamps to 5%
    lim = make_limits(
        regime="risk_off",
        verdicts={"X": "broken"},
        convictions={"X": 0.1},
    )
    cap = lim.ticker_limits["X"].single_name_cap
    assert cap == 0.05, f"Should be clamped to 5%, got {cap}"
    print("PASS: single_name_cap clamps to 5% min")


def test_not_reviewed_fallback():
    lim = make_limits(verdicts={}, convictions={})
    # ticker with no verdict/conviction → not_reviewed
    # not_reviewed verdict factor = 0.85, conviction = 0.85 (same as verdict)
    # 8% × 0.90 × 0.85 × 0.85 = 5.202% → clamp → 5.202%
    lim2 = make_limits(
        regime="neutral",
        verdicts={"NEW": "not_reviewed"},
        convictions={},
    )
    cap = lim2.ticker_limits["NEW"].single_name_cap
    assert 0.05 <= cap <= 0.12
    assert lim2.ticker_limits["NEW"].thesis_verdict == "not_reviewed"
    print(f"PASS: not_reviewed fallback -> cap={cap:.4f}, verdict shown as fact")


def test_group_cap():
    groups = {"NVDA": "semis", "AVGO": "semis"}
    verdicts = {"NVDA": "intact", "AVGO": "weakening"}
    lim = make_limits(regime="neutral", verdicts=verdicts, groups=groups)
    # avg verdict = (1.0 + 0.9) / 2 = 0.95
    # 18% × 0.90 × 0.95 = 15.39% → clamp [8%, 18%] → 15.39%
    cap = lim.group_caps["semis"]
    assert 0.08 <= cap <= 0.18
    assert abs(cap - 0.15390) < 0.001, f"semis group cap={cap:.5f}"
    print(f"PASS: group_cap semis={cap:.4f}")


# ── sizing tests ──────────────────────────────────────────────────────────────

def test_sizing_buy_basic():
    limits = make_limits(
        regime="neutral",
        verdicts={"MSFT": "intact"},
        convictions={"MSFT": 0.70},
    )
    intent = IntentInput("MSFT", "buy", conviction=0.70, limit_price=427.0, group="ai")
    plan = compute_order(intent, nav=304_200, last_price=427.0, limits=limits)

    assert plan.side == "buy"
    assert plan.shares > 0
    # size_pct = 0.70 × 4% = 2.8% → shares = floor(0.028 × 304200 / 427) = floor(19.946) = 19
    assert plan.shares == 19, f"Expected 19 shares, got {plan.shares}"
    actual = plan.shares * 427.0 / 304_200
    assert abs(plan.actual_pct - actual) < 1e-6
    print(f"PASS: buy sizing MSFT -- {plan.shares}sh @ 427 = {plan.actual_pct:.2%} NAV")


def test_sizing_cap_binding():
    # conviction=1.0 → raw 4% but single cap might be lower
    limits = make_limits(
        regime="risk_off",
        verdicts={"TSLA": "broken"},
        convictions={"TSLA": 1.0},
    )
    intent = IntentInput("TSLA", "buy", conviction=1.0, limit_price=200.0, group="consumer_tech")
    plan = compute_order(intent, nav=304_200, last_price=200.0, limits=limits)
    # broken + risk_off → single cap clamps to 5%
    assert plan.side == "buy"
    assert plan.size_pct <= 0.05, f"size_pct={plan.size_pct} should be <= 5%"
    assert plan.binding_cap == "single_name_cap"
    print(f"PASS: cap binding TSLA — size_pct={plan.size_pct:.2%} capped by {plan.binding_cap}")


def test_sell_or_avoid_held():
    limits = make_limits()
    intent = IntentInput("MRVL", "sell_or_avoid", conviction=0.0, limit_price=72.0)
    plan = compute_order(intent, nav=304_200, last_price=72.0, limits=limits, held_shares=76)

    assert plan.side == "sell"
    assert plan.shares == 76
    assert plan.is_market is True
    print(f"PASS: sell_or_avoid held -> sell {plan.shares}sh market")


def test_sell_or_avoid_not_held():
    limits = make_limits()
    intent = IntentInput("AAPL", "sell_or_avoid", conviction=0.0, limit_price=200.0)
    plan = compute_order(intent, nav=304_200, last_price=200.0, limits=limits, held_shares=0)

    assert plan.side == "skip"
    assert "not held" in plan.skipped_reason
    print(f"PASS: sell_or_avoid not held -> skip ({plan.skipped_reason})")


def test_event_lock_blocks_buy():
    limits = make_limits(regime="risk_on", event_lock=True)
    intent = IntentInput("MSFT", "buy", conviction=0.9, limit_price=427.0, group="ai")
    plan = compute_order(intent, nav=304_200, last_price=427.0, limits=limits)

    assert plan.side == "skip"
    assert "event_lock" in plan.skipped_reason
    print(f"PASS: event_lock blocks buy ({plan.skipped_reason})")


def test_defer_avoid_skip():
    limits = make_limits()
    for side in ("defer", "avoid"):
        intent = IntentInput("NVDA", side, conviction=0.5, limit_price=134.0)
        plan = compute_order(intent, nav=304_200, last_price=134.0, limits=limits)
        assert plan.side == "skip"
        assert side in plan.skipped_reason
    print("PASS: defer/avoid -> skip")


# ── state persistence tests ───────────────────────────────────────────────────

def test_conviction_roundtrip():
    save_conviction("MSFT", 0.75, source="execution_agent")
    val, status = get_conviction("MSFT")
    assert val == 0.75
    assert status == "reviewed"
    print("PASS: conviction save/load")


def test_conviction_not_reviewed():
    val, status = get_conviction("UNKNOWN_TICKER_XYZ")
    assert val is None
    assert status == "not_reviewed"
    print("PASS: conviction not_reviewed for unknown ticker")


def test_verdict_roundtrip():
    save_thesis_verdict("NVDA", "weakening")
    v = get_thesis_verdict("NVDA")
    assert v == "weakening"
    print("PASS: verdict save/load")


def test_verdict_invalid_raises():
    try:
        save_thesis_verdict("NVDA", "unknown_state")
        assert False, "Should raise"
    except ValueError:
        pass
    print("PASS: invalid verdict raises ValueError")


if __name__ == "__main__":
    print("\n--- Limit formulas ---")
    test_regime_factors()
    test_cash_floor_neutral()
    test_cash_floor_news_addon()
    test_cash_floor_clamp_max()
    test_gross_exposure_neutral()
    test_event_lock_kills_exposure()
    test_single_name_cap_spec_example()
    test_single_name_cap_clamp_min()
    test_not_reviewed_fallback()
    test_group_cap()

    print("\n--- Sizing ---")
    test_sizing_buy_basic()
    test_sizing_cap_binding()
    test_sell_or_avoid_held()
    test_sell_or_avoid_not_held()
    test_event_lock_blocks_buy()
    test_defer_avoid_skip()

    print("\n--- State persistence ---")
    test_conviction_roundtrip()
    test_conviction_not_reviewed()
    test_verdict_roundtrip()
    test_verdict_invalid_raises()

    print("\nAll core tests passed.")
    try:
        os.unlink(tmp.name)
    except Exception:
        pass
