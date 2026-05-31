"""
Deterministic sizing logic — spec §4.2 + §4.3.

Formula:
  size_pct = conviction × base 4% of NAV → min(all applicable caps)
  shares   = floor(size_pct × NAV / last_price)   # integer first
  actual_pct = shares × last_price / NAV           # derived back

sell_or_avoid resolution (spec §4.3):
  if held → SELL all held shares (market order)
  if not held → skip (avoid)
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Literal

from .limits import LimitOutputs

BASE_SIZE_PCT = 0.04  # 4% of NAV base


@dataclass
class IntentInput:
    """One trade intent from Execution agent (or hand-typed in Phase 1)."""

    ticker: str
    side: Literal["buy", "sell_or_avoid", "defer", "avoid"]
    conviction: float           # 0.0–1.0
    limit_price: float          # agent's entry price
    group: str | None = None    # for group cap check


@dataclass
class OrderPlan:
    """Sized order ready for Paper Executor."""

    ticker: str
    side: Literal["buy", "sell", "skip"]
    shares: int
    size_pct: float             # intended % of NAV
    actual_pct: float           # derived after integer shares
    limit_price: float
    is_market: bool             # True for forced sells
    binding_cap: str            # which cap was the binding constraint
    conviction: float
    skipped_reason: str | None  # why side='skip'


def compute_order(
    intent: IntentInput,
    nav: float,
    last_price: float,
    limits: LimitOutputs,
    held_shares: int = 0,
) -> OrderPlan:
    """
    Convert one Execution intent into a sized order plan.

    Args:
        intent:      trade intent from Execution agent
        nav:         current portfolio NAV ($)
        last_price:  latest price for the ticker ($)
        limits:      computed LimitOutputs for this run
        held_shares: shares currently held (for sell_or_avoid)
    """
    ticker = intent.ticker

    # --- defer / avoid → skip immediately ---
    if intent.side in ("defer", "avoid"):
        return _skip(intent, f"intent={intent.side}")

    # --- sell_or_avoid resolution (spec §4.3) ---
    if intent.side == "sell_or_avoid":
        if held_shares > 0:
            actual_pct = round(held_shares * last_price / nav, 6)
            return OrderPlan(
                ticker=ticker,
                side="sell",
                shares=held_shares,
                size_pct=actual_pct,
                actual_pct=actual_pct,
                limit_price=last_price,
                is_market=True,
                binding_cap="sell_or_avoid_resolution",
                conviction=intent.conviction,
                skipped_reason=None,
            )
        else:
            return _skip(intent, "sell_or_avoid: not held -> avoid")

    # --- buy sizing ---
    # event lock: no new buys
    if limits.event_lock:
        return _skip(intent, "event_lock=True: no new buys")

    # collect all applicable caps
    ticker_limits = limits.ticker_limits.get(ticker)
    single_cap = ticker_limits.single_name_cap if ticker_limits else 0.08

    group = intent.group or ""
    group_cap = limits.group_caps.get(group)

    caps: list[tuple[str, float]] = [
        ("single_name_cap", single_cap),
        ("gross_exposure_cap", limits.gross_exposure_cap),
    ]
    if group_cap is not None:
        caps.append((f"group_cap:{group}", group_cap))

    # size_pct = conviction × 4% → min of all caps
    size_pct_raw = intent.conviction * BASE_SIZE_PCT
    binding_name, binding_cap_val = min(caps, key=lambda x: x[1])
    size_pct = min(size_pct_raw, binding_cap_val)

    if size_pct <= 0 or last_price <= 0:
        return _skip(intent, f"size_pct={size_pct:.4f} or price={last_price}")

    # integer shares first
    shares = math.floor(size_pct * nav / last_price)
    if shares <= 0:
        return _skip(intent, f"shares=0 after floor (size_pct={size_pct:.4f}, nav={nav}, price={last_price})")

    actual_pct = round(shares * last_price / nav, 6)

    return OrderPlan(
        ticker=ticker,
        side="buy",
        shares=shares,
        size_pct=round(size_pct, 6),
        actual_pct=actual_pct,
        limit_price=intent.limit_price,
        is_market=False,
        binding_cap=binding_name,
        conviction=intent.conviction,
        skipped_reason=None,
    )


def _skip(intent: IntentInput, reason: str) -> OrderPlan:
    return OrderPlan(
        ticker=intent.ticker,
        side="skip",
        shares=0,
        size_pct=0.0,
        actual_pct=0.0,
        limit_price=intent.limit_price,
        is_market=False,
        binding_cap="none",
        conviction=intent.conviction,
        skipped_reason=reason,
    )
