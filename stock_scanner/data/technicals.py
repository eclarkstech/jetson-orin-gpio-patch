"""
data/technicals.py — Fetch price/volume technicals for the movement-potential layer.

Metrics returned per ticker
────────────────────────────
relative_volume     Today's volume / 20-day average volume
breakout_strength   (current_price − 52w_low) / (52w_high − 52w_low)
                    0 = at 52-week low, 1 = at 52-week high
price_vs_sma50      current_price / SMA(50) − 1  (positive = above MA)
price_vs_sma200     current_price / SMA(200) − 1
atr_pct             Average True Range / price  (volatility proxy)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import yfinance as yf
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class TechnicalData:
    ticker: str
    relative_volume: Optional[float] = None
    breakout_strength: Optional[float] = None
    price_vs_sma50: Optional[float] = None
    price_vs_sma200: Optional[float] = None
    atr_pct: Optional[float] = None
    week52_high: Optional[float] = None
    week52_low: Optional[float] = None
    error: Optional[str] = None


def _compute_atr(hist: pd.DataFrame, period: int = 14) -> Optional[float]:
    """Average True Range over `period` days."""
    if hist is None or len(hist) < period + 1:
        return None
    high = hist["High"]
    low = hist["Low"]
    close_prev = hist["Close"].shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - close_prev).abs(),
            (low - close_prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]


def fetch_technicals(ticker: str, lookback_days: int = 252) -> TechnicalData:
    """Return a TechnicalData instance populated from yfinance price history."""
    result = TechnicalData(ticker=ticker)
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="1y", auto_adjust=True)

        if hist is None or hist.empty:
            result.error = "no price history"
            return result

        close = hist["Close"]
        volume = hist["Volume"]

        # ── 52-week range ────────────────────────────────────────────────
        result.week52_high = close.max()
        result.week52_low = close.min()
        current_price = close.iloc[-1]

        # ── Breakout strength ────────────────────────────────────────────
        # Score = 1 − distance_from_52w_high (0 = far below high, 1 = at high)
        price_range = result.week52_high - result.week52_low
        if price_range > 0:
            result.breakout_strength = (current_price - result.week52_low) / price_range
        else:
            result.breakout_strength = 0.5

        # ── Relative volume ──────────────────────────────────────────────
        avg_20d_vol = volume.iloc[-21:-1].mean() if len(volume) >= 21 else volume.mean()
        today_vol = volume.iloc[-1]
        result.relative_volume = today_vol / avg_20d_vol if avg_20d_vol > 0 else None

        # ── Moving averages ──────────────────────────────────────────────
        if len(close) >= 50:
            sma50 = close.rolling(50).mean().iloc[-1]
            result.price_vs_sma50 = (current_price / sma50 - 1) if sma50 > 0 else None
        if len(close) >= 200:
            sma200 = close.rolling(200).mean().iloc[-1]
            result.price_vs_sma200 = (current_price / sma200 - 1) if sma200 > 0 else None

        # ── ATR (volatility) ─────────────────────────────────────────────
        atr = _compute_atr(hist)
        result.atr_pct = atr / current_price if (atr and current_price > 0) else None

    except Exception as exc:
        logger.warning("technicals fetch failed for %s: %s", ticker, exc)
        result.error = str(exc)

    return result
