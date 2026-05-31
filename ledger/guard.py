"""
Look-ahead bias guard — existential safety check (spec §9).

Every data packet built for an agent must pass through assert_no_lookahead()
before being handed to the LLM. This ensures no data point has a timestamp
after the decision cutoff.

Rule: data_ts <= cutoff_ts strictly. Even same-second is fine.
"""

from __future__ import annotations
from datetime import datetime, timezone


class LookAheadError(Exception):
    """Raised when a data point post-dates the decision cutoff."""


def parse_ts(ts: str | datetime) -> datetime:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def assert_no_lookahead(
    data_points: list[dict],
    cutoff_ts: str | datetime,
    ts_field: str = "ts",
) -> None:
    """
    Assert that every record in data_points has ts_field <= cutoff_ts.

    Args:
        data_points: list of dicts, each with a timestamp field.
        cutoff_ts:   the decision cutoff (run timestamp).
        ts_field:    key name for the timestamp in each dict.

    Raises:
        LookAheadError with details of the first violation found.
    """
    cutoff = parse_ts(cutoff_ts)

    for i, point in enumerate(data_points):
        raw = point.get(ts_field)
        if raw is None:
            raise LookAheadError(
                f"Record [{i}] missing timestamp field '{ts_field}': {point}"
            )
        data_dt = parse_ts(raw)
        if data_dt > cutoff:
            raise LookAheadError(
                f"Look-ahead violation at record [{i}]: "
                f"data_ts={data_dt.isoformat()} > cutoff={cutoff.isoformat()} "
                f"(ticker={point.get('ticker', '?')})"
            )


def make_cutoff(run_ts: str | datetime | None = None) -> datetime:
    """Return the decision cutoff: now() UTC, or the provided run_ts."""
    if run_ts is None:
        return datetime.now(timezone.utc)
    return parse_ts(run_ts)
