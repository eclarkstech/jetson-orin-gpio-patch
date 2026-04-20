"""
scoring/movement.py — Movement-potential layer score.

Combines:
  • Relative volume    — unusual trading activity signals institutional interest
  • Breakout strength  — proximity to 52-week high suggests price momentum
  • Earnings momentum  — recent EPS beat / analyst revision trend

Final movement_score ∈ [0, 1].
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional

from stock_scanner.config import MOVEMENT_WEIGHTS
from stock_scanner.data.technicals import TechnicalData
from stock_scanner.data.catalysts import CatalystData


@dataclass
class MovementScore:
    ticker: str
    total: float = 0.0
    components: Dict[str, float] = field(default_factory=dict)


def _normalise_relative_volume(rv: Optional[float]) -> float:
    """
    rv=1.0 → no unusual activity → 0.5
    rv=2.0 → double average volume → ~0.73
    rv=3.0 → triple → ~0.88
    rv<1.0 → below average → <0.5
    """
    if rv is None:
        return 0.30
    # Sigmoid centred at 1.0 (average volume), scale=0.5
    return 1.0 / (1.0 + math.exp(-(rv - 1.0) / 0.5))


def _normalise_breakout_strength(bs: Optional[float]) -> float:
    """
    breakout_strength is already ∈ [0, 1]:
      1.0 = at 52-week high (strongest breakout signal)
      0.0 = at 52-week low
    Apply a slight curve to reward names that are near — but have not yet exceeded —
    their previous high (i.e., in the 80–100 % zone).
    """
    if bs is None:
        return 0.30
    # Reward the upper half more aggressively
    return bs ** 0.7  # convex curve; still ∈ [0, 1]


def _normalise_earnings_momentum(catalyst: Optional[CatalystData]) -> float:
    """
    Blend earnings proximity and analyst revision into a single 0-1 score.
    """
    if catalyst is None:
        return 0.30
    eps_score = catalyst.earnings_proximity_score              # already [0,1]
    revision_norm = (catalyst.analyst_revision_score + 1.0) / 2.0  # [-1,1] → [0,1]
    return 0.5 * eps_score + 0.5 * revision_norm


def compute_movement_score(
    tech: TechnicalData,
    catalyst: Optional[CatalystData] = None,
    weights: dict[str, float] = MOVEMENT_WEIGHTS,
) -> MovementScore:
    """Compute weighted movement-potential score ∈ [0, 1] for one ticker."""
    score = MovementScore(ticker=tech.ticker)

    components = {
        "relative_volume":   _normalise_relative_volume(tech.relative_volume),
        "breakout_strength": _normalise_breakout_strength(tech.breakout_strength),
        "earnings_momentum": _normalise_earnings_momentum(catalyst),
    }
    score.components = components
    score.total = sum(weights.get(k, 0) * v for k, v in components.items())
    return score
