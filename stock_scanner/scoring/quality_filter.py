"""
scoring/quality_filter.py — Hard gates that must pass before a ticker is scored.

A ticker is REJECTED if any of the following are true:
  • Price below minimum (penny-stock filter)
  • Market cap below minimum
  • Free cash flow is negative (if the flag is enabled)
  • Debt-to-equity exceeds the ceiling
  • Current ratio below the floor
  • Gross margin below the floor
  • Gross margin is declining (if the flag is enabled)

Returns a (passed: bool, reasons: list[str]) tuple so callers can log
why each name was filtered out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from stock_scanner.config import QUALITY_THRESHOLDS, QualityThresholds
from stock_scanner.data.fundamentals import FundamentalData


@dataclass
class FilterResult:
    passed: bool
    reasons: List[str]


def apply_quality_filters(
    data: FundamentalData,
    thresholds: QualityThresholds = QUALITY_THRESHOLDS,
) -> FilterResult:
    """
    Run all quality gates against the supplied FundamentalData.

    Returns FilterResult(passed=True, reasons=[]) when the ticker
    clears every gate, or FilterResult(passed=False, reasons=[...])
    listing every gate it failed.
    """
    failures: List[str] = []

    # ── 1. Price ─────────────────────────────────────────────────────────
    if data.price is None:
        failures.append("price unavailable")
    elif data.price < thresholds.min_price:
        failures.append(
            f"price ${data.price:.2f} below minimum ${thresholds.min_price:.2f}"
        )

    # ── 2. Market cap ─────────────────────────────────────────────────────
    if data.market_cap is None:
        failures.append("market cap unavailable")
    elif data.market_cap < thresholds.min_market_cap_m * 1_000_000:
        actual_m = data.market_cap / 1_000_000
        failures.append(
            f"market cap ${actual_m:.0f}M below minimum ${thresholds.min_market_cap_m:.0f}M"
        )

    # ── 3. Positive FCF ───────────────────────────────────────────────────
    if thresholds.require_positive_fcf:
        if data.trailing_fcf is None:
            failures.append("FCF data unavailable")
        elif data.trailing_fcf <= 0:
            failures.append(f"negative FCF ({data.trailing_fcf:,.0f})")

    # ── 4. Debt / equity ──────────────────────────────────────────────────
    if data.debt_to_equity is not None:
        if data.debt_to_equity > thresholds.max_debt_to_equity:
            failures.append(
                f"D/E {data.debt_to_equity:.2f} exceeds ceiling {thresholds.max_debt_to_equity:.2f}"
            )

    # ── 5. Current ratio ──────────────────────────────────────────────────
    if data.current_ratio is not None:
        if data.current_ratio < thresholds.min_current_ratio:
            failures.append(
                f"current ratio {data.current_ratio:.2f} below floor {thresholds.min_current_ratio:.2f}"
            )

    # ── 6. Gross margin floor ─────────────────────────────────────────────
    if data.gross_margin is not None:
        if data.gross_margin < thresholds.min_gross_margin:
            failures.append(
                f"gross margin {data.gross_margin:.1%} below floor {thresholds.min_gross_margin:.1%}"
            )

    # ── 7. Improving gross margin ─────────────────────────────────────────
    if thresholds.require_improving_gross_margin:
        if data.gross_margin is not None and data.prev_gross_margin is not None:
            if data.gross_margin < data.prev_gross_margin:
                failures.append(
                    f"gross margin declining "
                    f"({data.prev_gross_margin:.1%} → {data.gross_margin:.1%})"
                )

    return FilterResult(passed=len(failures) == 0, reasons=failures)
