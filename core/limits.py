"""
Deterministic limit formulas — spec §4.1.

ALL limits are computed from prior-agent OUTCOMES.
No agent and no user can set these. (Anti-Goodhart rule.)

Formula summary:
  single_name_cap  = base 8%  × regime × thesis_verdict × conviction  → clamp [5%, 12%]
  cash_floor       = base 10% × (2 - regime) + news_addon             → clamp [10%, 25%]
  group_cap        = base 18% × regime × group_thesis_avg             → clamp [8%, 18%]
  gross_exposure   = base 85% × regime × (1 - event_lock)            → clamp [50%, 90%]

Regime factor mapping (spec §4):
  risk_on  → 1.00
  neutral  → 0.90
  risk_off → 0.80

Thesis verdict → factor:
  intact      → 1.00
  weakening   → 0.90
  watch       → 0.85
  broken      → 0.70
  not_reviewed → 0.85  (conservative; signals "no agent output yet")
"""

from __future__ import annotations
from dataclasses import dataclass, field


# --- constants ---

REGIME_FACTOR: dict[str, float] = {
    "risk_on": 1.00,
    "neutral": 0.90,
    "risk_off": 0.80,
}

VERDICT_FACTOR: dict[str, float] = {
    "intact": 1.00,
    "weakening": 0.90,
    "watch": 0.85,
    "broken": 0.70,
    "not_reviewed": 0.85,
}

# News addon to cash floor: flat +2% per ticker flagged as material by News agent
NEWS_ADDON_PER_FLAG = 0.02


@dataclass
class LimitInputs:
    """All inputs needed to compute limits for one run."""

    # From Macro agent (or fallback)
    regime: str = "neutral"         # "risk_on" | "neutral" | "risk_off"
    event_lock: bool = False        # True → no new buys

    # Per-ticker thesis verdict {ticker: verdict_str}
    thesis_verdicts: dict[str, str] = field(default_factory=dict)

    # Per-ticker conviction from Execution agent {ticker: float 0-1}
    convictions: dict[str, float] = field(default_factory=dict)

    # Group membership {ticker: group_name}
    groups: dict[str, str] = field(default_factory=dict)

    # News-flagged tickers (material events) from News agent
    news_flagged: list[str] = field(default_factory=list)


@dataclass
class TickerLimits:
    single_name_cap: float          # max % of NAV for this ticker
    thesis_verdict: str
    conviction: float
    computation: dict               # shows every factor for audit trail


@dataclass
class LimitOutputs:
    """All computed limits for this run. Single source of truth for Risk agent."""

    # Portfolio-level limits
    cash_floor: float               # minimum cash % of NAV
    gross_exposure_cap: float       # maximum invested % of NAV
    event_lock: bool

    # Per-group caps {group_name: cap_pct}
    group_caps: dict[str, float]

    # Per-ticker limits
    ticker_limits: dict[str, TickerLimits]

    # Regime factor used (for display)
    regime: str
    regime_factor: float

    # News addon that pushed cash floor up
    news_addon: float


def compute_limits(inputs: LimitInputs) -> LimitOutputs:
    """
    Compute all deterministic limits from agent outcomes.
    Pure function — same inputs always produce same outputs.
    """
    regime_f = REGIME_FACTOR.get(inputs.regime, 0.90)

    # --- cash floor ---
    news_addon = min(len(inputs.news_flagged) * NEWS_ADDON_PER_FLAG, 0.10)  # cap addon at +10%
    cash_floor_raw = 0.10 * (2 - regime_f) + news_addon
    cash_floor = _clamp(cash_floor_raw, 0.10, 0.25)

    # --- gross exposure ---
    event_lock_factor = 0.0 if inputs.event_lock else 1.0
    gross_raw = 0.85 * regime_f * event_lock_factor
    gross_exposure_cap = _clamp(gross_raw, 0.50, 0.90)

    # --- per-group caps ---
    group_caps: dict[str, float] = {}
    group_tickers: dict[str, list[str]] = {}
    for ticker, group in inputs.groups.items():
        group_tickers.setdefault(group, []).append(ticker)

    for group, members in group_tickers.items():
        verdicts = [
            VERDICT_FACTOR.get(
                inputs.thesis_verdicts.get(t, "not_reviewed"), 0.85
            )
            for t in members
        ]
        avg_verdict = sum(verdicts) / len(verdicts)
        group_raw = 0.18 * regime_f * avg_verdict
        group_caps[group] = _clamp(group_raw, 0.08, 0.18)

    # --- per-ticker single-name caps ---
    all_tickers = set(inputs.groups.keys()) | set(inputs.thesis_verdicts.keys()) | set(inputs.convictions.keys())
    ticker_limits: dict[str, TickerLimits] = {}

    for ticker in all_tickers:
        verdict_str = inputs.thesis_verdicts.get(ticker, "not_reviewed")
        verdict_f = VERDICT_FACTOR.get(verdict_str, 0.85)
        conviction = inputs.convictions.get(ticker, None)

        if conviction is None:
            # No execution output yet — use verdict-scaled base
            conviction_f = verdict_f
            conviction_display = "not_reviewed"
        else:
            conviction_f = float(conviction)
            conviction_display = conviction

        single_raw = 0.08 * regime_f * verdict_f * conviction_f
        single_cap = _clamp(single_raw, 0.05, 0.12)

        ticker_limits[ticker] = TickerLimits(
            single_name_cap=single_cap,
            thesis_verdict=verdict_str,
            conviction=conviction_display,
            computation={
                "base": 0.08,
                "regime_factor": regime_f,
                "verdict_factor": verdict_f,
                "conviction": conviction_f,
                "raw": round(single_raw, 6),
                "clamped": single_cap,
                "clamp_range": [0.05, 0.12],
            },
        )

    return LimitOutputs(
        cash_floor=cash_floor,
        gross_exposure_cap=gross_exposure_cap,
        event_lock=inputs.event_lock,
        group_caps=group_caps,
        ticker_limits=ticker_limits,
        regime=inputs.regime,
        regime_factor=regime_f,
        news_addon=news_addon,
    )


def _clamp(value: float, lo: float, hi: float) -> float:
    return round(max(lo, min(hi, value)), 6)
