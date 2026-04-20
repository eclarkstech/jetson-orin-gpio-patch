"""
scoring/scorer.py — Weighted fundamental score.

Each raw metric is normalised to [0, 1] via a sigmoid-like soft clamp so that
extreme outliers don't distort the ranking while still being rewarded.

Final fundamental_score ∈ [0, 1].
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional

from stock_scanner.config import FUNDAMENTAL_WEIGHTS
from stock_scanner.data.fundamentals import FundamentalData


@dataclass
class FundamentalScore:
    ticker: str
    total: float = 0.0
    components: Dict[str, float] = field(default_factory=dict)
    raw_metrics: Dict[str, Optional[float]] = field(default_factory=dict)


def _sigmoid_normalise(value: Optional[float], midpoint: float, scale: float) -> float:
    """
    Map any real value to (0, 1) using a logistic function centred at `midpoint`.
    scale controls how quickly the score saturates (smaller = steeper).

    Examples with midpoint=0.10, scale=0.20:
      value=0.00 → ~0.38  (flat / no growth)
      value=0.10 → ~0.50  (at midpoint)
      value=0.30 → ~0.73  (strong growth)
      value=0.50 → ~0.88  (very strong)
    """
    if value is None:
        # Missing data is penalized but not zero — reward companies that report
        return 0.30
    return 1.0 / (1.0 + math.exp(-(value - midpoint) / scale))


def _normalise_revenue_growth(v: Optional[float]) -> float:
    # Midpoint at 10 % YoY growth; saturates around 40 %
    return _sigmoid_normalise(v, midpoint=0.10, scale=0.15)


def _normalise_eps_growth(v: Optional[float]) -> float:
    # EPS growth is noisier; wider scale
    return _sigmoid_normalise(v, midpoint=0.10, scale=0.20)


def _normalise_fcf_growth(v: Optional[float]) -> float:
    return _sigmoid_normalise(v, midpoint=0.10, scale=0.15)


def _normalise_roic(v: Optional[float]) -> float:
    # ROIC midpoint at 15 % (roughly cost of capital for most companies)
    return _sigmoid_normalise(v, midpoint=0.15, scale=0.10)


def _normalise_buyback_yield(v: Optional[float]) -> float:
    # Positive = net share reduction.  Midpoint at 1 % yield; saturates at ~3-4 %
    return _sigmoid_normalise(v, midpoint=0.01, scale=0.015)


_NORMALISERS = {
    "revenue_growth": _normalise_revenue_growth,
    "eps_growth": _normalise_eps_growth,
    "fcf_growth": _normalise_fcf_growth,
    "roic": _normalise_roic,
    "buyback_yield": _normalise_buyback_yield,
}


def compute_fundamental_score(
    data: FundamentalData,
    weights: dict[str, float] = FUNDAMENTAL_WEIGHTS,
) -> FundamentalScore:
    """Compute a weighted fundamental score ∈ [0, 1] for one ticker."""
    score = FundamentalScore(ticker=data.ticker)

    raw = {
        "revenue_growth": data.revenue_growth,
        "eps_growth":     data.eps_growth,
        "fcf_growth":     data.fcf_growth,
        "roic":           data.roic,
        "buyback_yield":  data.buyback_yield,
    }
    score.raw_metrics = raw

    total = 0.0
    for metric, weight in weights.items():
        normaliser = _NORMALISERS.get(metric)
        if normaliser is None:
            continue
        component = normaliser(raw.get(metric))
        score.components[metric] = component
        total += weight * component

    score.total = total
    return score
