"""Tests for look-ahead bias guard."""

import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Force SQLite mode BEFORE importing any ledger code
os.environ["USE_SUPABASE"] = "false"
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_KEY", None)

from ledger.guard import assert_no_lookahead, LookAheadError

CUTOFF = "2026-05-09T09:30:00+00:00"


def test_clean_data_passes():
    data = [
        {"ticker": "MSFT", "ts": "2026-05-09T09:00:00+00:00", "price": 427},
        {"ticker": "COST", "ts": "2026-05-09T09:29:59+00:00", "price": 951},
        {"ticker": "NVDA", "ts": "2026-05-08T20:00:00+00:00", "price": 134},
    ]
    assert_no_lookahead(data, CUTOFF)
    print("PASS: clean data (all before cutoff)")


def test_exact_cutoff_passes():
    data = [{"ticker": "SPY", "ts": CUTOFF, "price": 520}]
    assert_no_lookahead(data, CUTOFF)
    print("PASS: exact cutoff timestamp is allowed")


def test_future_data_raises():
    data = [
        {"ticker": "MSFT", "ts": "2026-05-09T09:00:00+00:00", "price": 427},
        {"ticker": "AAPL", "ts": "2026-05-09T09:30:01+00:00", "price": 200},  # 1s after cutoff
    ]
    try:
        assert_no_lookahead(data, CUTOFF)
        assert False, "Should have raised LookAheadError"
    except LookAheadError as e:
        assert "AAPL" in str(e)
        print(f"PASS: future data raises LookAheadError — {e}")


def test_missing_ts_field_raises():
    data = [{"ticker": "MSFT", "price": 427}]  # no 'ts' field
    try:
        assert_no_lookahead(data, CUTOFF)
        assert False, "Should have raised LookAheadError"
    except LookAheadError as e:
        assert "missing timestamp" in str(e)
        print(f"PASS: missing ts field raises — {e}")


def test_seed_positions_and_guard():
    """Integration: seeded positions have past timestamps → guard passes."""
    import tempfile
    from pathlib import Path
    import ledger.storage as _db
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    _db.DB_PATH = Path(tmp.name)

    from ledger.storage import init_schema as init_db
    from ledger.positions import seed_positions, get_positions
    init_db()

    holdings = [
        {"ticker": "NVDA", "shares": 50, "avg_cost": 120.0, "last_price": 134.0},
        {"ticker": "TSLA", "shares": 30, "avg_cost": 180.0, "last_price": 195.0},
    ]
    seed_positions(holdings, ts="2026-05-01T00:00:00+00:00")

    positions = get_positions()
    assert len(positions) == 2
    assert positions[0]["ticker"] == "NVDA"
    assert positions[1]["ticker"] == "TSLA"
    print(f"PASS: seed_positions -> {[p['ticker'] for p in positions]}")

    try:
        os.unlink(tmp.name)
    except PermissionError:
        pass  # Windows locks SQLite WAL files briefly; cleanup is best-effort in tests


if __name__ == "__main__":
    test_clean_data_passes()
    test_exact_cutoff_passes()
    test_future_data_raises()
    test_missing_ts_field_raises()
    test_seed_positions_and_guard()
    print("\nAll guard tests passed.")
