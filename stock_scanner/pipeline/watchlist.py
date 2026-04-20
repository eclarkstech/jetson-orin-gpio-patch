"""
pipeline/watchlist.py — Core refresh pipeline.

WatchlistPipeline orchestrates:
  1. Fetch fundamentals, technicals, and catalysts for every ticker in the universe.
  2. Apply quality/risk filters — drop tickers that fail hard gates.
  3. Compute fundamental, movement, and catalyst scores.
  4. Blend scores into a composite and rank all passing tickers.
  5. Return top-N WatchlistEntry objects ready for notification.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import List, Optional

from stock_scanner.config import (
    CATALYST_WEIGHTS,
    COMPOSITE_WEIGHTS,
    PIPELINE_SETTINGS,
    DEFAULT_UNIVERSE,
)
from stock_scanner.data.fundamentals import FundamentalData, fetch_fundamentals
from stock_scanner.data.technicals import TechnicalData, fetch_technicals
from stock_scanner.data.catalysts import CatalystData, fetch_catalysts
from stock_scanner.scoring.quality_filter import FilterResult, apply_quality_filters
from stock_scanner.scoring.scorer import FundamentalScore, compute_fundamental_score
from stock_scanner.scoring.movement import MovementScore, compute_movement_score

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Catalyst score helper
# ────────────────────────────────────────────────────────────────────────────

def _compute_catalyst_score(catalyst: CatalystData) -> float:
    """Blend individual catalyst signals into a single ∈ [0, 1] score."""
    # All raw signals from CatalystData are ∈ [-1, 1] or [0, 1]
    # Map [-1, 1] → [0, 1] where needed
    earnings   = catalyst.earnings_proximity_score          # already [0, 1]
    insider    = (catalyst.insider_score + 1.0) / 2.0       # [-1,1] → [0,1]
    sentiment  = (catalyst.news_sentiment_score + 1.0) / 2.0
    revision   = (catalyst.analyst_revision_score + 1.0) / 2.0

    return (
        CATALYST_WEIGHTS["earnings_proximity"] * earnings
        + CATALYST_WEIGHTS["insider_activity"]  * insider
        + CATALYST_WEIGHTS["news_sentiment"]    * sentiment
        + CATALYST_WEIGHTS["analyst_revision"]  * revision
    )


# ────────────────────────────────────────────────────────────────────────────
# Data container for a single ticker's full evaluation result
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class WatchlistEntry:
    ticker: str
    composite_score: float = 0.0
    fundamental_score: float = 0.0
    movement_score: float = 0.0
    catalyst_score: float = 0.0

    # Raw data objects (kept for the notifier to build rationale)
    fundamentals: Optional[FundamentalData] = None
    technicals: Optional[TechnicalData] = None
    catalysts: Optional[CatalystData] = None
    fund_score_detail: Optional[FundamentalScore] = None
    move_score_detail: Optional[MovementScore] = None

    filter_result: Optional[FilterResult] = None

    def __lt__(self, other: "WatchlistEntry") -> bool:
        return self.composite_score < other.composite_score


# ────────────────────────────────────────────────────────────────────────────
# Per-ticker evaluation
# ────────────────────────────────────────────────────────────────────────────

def _evaluate_ticker(ticker: str) -> WatchlistEntry:
    entry = WatchlistEntry(ticker=ticker)

    # 1. Fetch all data
    entry.fundamentals = fetch_fundamentals(ticker)
    entry.technicals   = fetch_technicals(ticker)
    entry.catalysts    = fetch_catalysts(ticker)

    # 2. Quality filter
    entry.filter_result = apply_quality_filters(entry.fundamentals)
    if not entry.filter_result.passed:
        logger.info(
            "FILTERED %s: %s",
            ticker,
            "; ".join(entry.filter_result.reasons),
        )
        return entry  # composite_score stays 0

    # 3. Score
    fund = compute_fundamental_score(entry.fundamentals)
    move = compute_movement_score(entry.technicals, entry.catalysts)
    cat_score = _compute_catalyst_score(entry.catalysts)

    entry.fund_score_detail  = fund
    entry.move_score_detail  = move
    entry.fundamental_score  = fund.total
    entry.movement_score     = move.total
    entry.catalyst_score     = cat_score

    # 4. Composite blend
    entry.composite_score = (
        COMPOSITE_WEIGHTS["fundamental"] * fund.total
        + COMPOSITE_WEIGHTS["movement"]   * move.total
        + COMPOSITE_WEIGHTS["catalyst"]   * cat_score
    )

    return entry


# ────────────────────────────────────────────────────────────────────────────
# Pipeline
# ────────────────────────────────────────────────────────────────────────────

class WatchlistPipeline:
    """
    Orchestrates the full refresh cycle.

    Parameters
    ----------
    universe : list of ticker symbols to evaluate
    top_n    : how many top-ranked entries to return
    max_workers : thread-pool size for concurrent data fetching
    """

    def __init__(
        self,
        universe: Optional[List[str]] = None,
        top_n: Optional[int] = None,
        max_workers: int = 8,
    ) -> None:
        self.universe = universe or DEFAULT_UNIVERSE
        self.top_n = top_n or PIPELINE_SETTINGS.top_n_picks
        self.max_workers = max_workers

    # ── Public API ────────────────────────────────────────────────────────

    def run(self) -> List[WatchlistEntry]:
        """
        Execute one full refresh cycle.

        Returns
        -------
        List[WatchlistEntry]
            Top-N entries sorted by composite_score descending.
        """
        logger.info(
            "Starting watchlist refresh for %d tickers …", len(self.universe)
        )
        start = time.monotonic()
        results: List[WatchlistEntry] = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(_evaluate_ticker, t): t for t in self.universe}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    entry = future.result()
                    results.append(entry)
                except Exception as exc:
                    logger.error("Unhandled error evaluating %s: %s", ticker, exc)
                    results.append(WatchlistEntry(ticker=ticker))

        elapsed = time.monotonic() - start
        logger.info("Refresh completed in %.1f s", elapsed)

        # Sort by composite score, highest first; filtered names (score=0) sink to bottom
        results.sort(key=lambda e: e.composite_score, reverse=True)

        # Only return tickers that passed the quality filter
        passed = [e for e in results if e.filter_result and e.filter_result.passed]
        return passed[: self.top_n]

    def run_single(self, ticker: str) -> WatchlistEntry:
        """Evaluate a single ticker outside the normal universe (useful for ad-hoc checks)."""
        return _evaluate_ticker(ticker)
