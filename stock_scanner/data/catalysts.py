"""
data/catalysts.py — Fetch catalyst signals via Finnhub (free tier) and yfinance.

Catalyst signals
────────────────────────────────────────────────────────────────
earnings_proximity_score   Normalised score based on how many days until next earnings
                           (higher score = earnings is sooner, within the configured window)
insider_score              Net insider-buy score over the past 90 days
news_sentiment_score       Averaged news sentiment from Finnhub headlines
analyst_revision_score     Net analyst EPS revision direction from Finnhub
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Union

import requests
import yfinance as yf

from stock_scanner.config import FINNHUB_API_KEY, PIPELINE_SETTINGS

logger = logging.getLogger(__name__)

_FINNHUB_BASE = "https://finnhub.io/api/v1"
_REQUEST_TIMEOUT = 10  # seconds


def _finnhub_get(endpoint: str, params: dict) -> Optional[Union[dict, list]]:
    """Make a Finnhub API call; return parsed JSON or None on failure."""
    if not FINNHUB_API_KEY:
        return None
    params["token"] = FINNHUB_API_KEY
    try:
        resp = requests.get(
            f"{_FINNHUB_BASE}/{endpoint}",
            params=params,
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.debug("Finnhub request failed (%s): %s", endpoint, exc)
        return None


@dataclass
class CatalystData:
    ticker: str
    earnings_proximity_score: float = 0.0
    insider_score: float = 0.0
    news_sentiment_score: float = 0.0
    analyst_revision_score: float = 0.0
    next_earnings_date: Optional[str] = None
    days_to_earnings: Optional[int] = None
    recent_news: List[str] = field(default_factory=list)
    error: Optional[str] = None


# ────────────────────────────────────────────────────────────────────────────
# Earnings proximity
# ────────────────────────────────────────────────────────────────────────────

def _earnings_proximity_score(days: Optional[int], window: int) -> float:
    """
    Score ∈ [0, 1].
    - days is None → 0
    - days <= 0 (already passed) → 0
    - days within window → linearly higher as it approaches (max at 1 day out = 1.0)
    - days beyond window → 0
    """
    if days is None or days <= 0 or days > window:
        return 0.0
    return 1.0 - (days - 1) / window


def _fetch_next_earnings(ticker: str) -> tuple[Optional[str], Optional[int]]:
    """Return (date_str, days_to_earnings) using Finnhub, fallback to yfinance."""
    today = datetime.now(tz=timezone.utc).date()

    # Try Finnhub first
    data = _finnhub_get("calendar/earnings", {
        "from": today.isoformat(),
        "to": (today + timedelta(days=90)).isoformat(),
        "symbol": ticker,
    })
    if data and isinstance(data, dict):
        earnings_list = data.get("earningsCalendar", [])
        if earnings_list:
            date_str = earnings_list[0].get("date", "")
            try:
                earnings_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                days = (earnings_date - today).days
                return date_str, days
            except ValueError:
                pass

    # Fallback: yfinance calendar
    try:
        cal = yf.Ticker(ticker).calendar
        if cal is not None and not cal.empty:
            # calendar is a DataFrame with dates in columns
            if "Earnings Date" in cal.index:
                date_val = cal.loc["Earnings Date"].iloc[0]
                if hasattr(date_val, "date"):
                    date_val = date_val.date()
                days = (date_val - today).days
                return str(date_val), days
    except Exception:
        pass

    return None, None


# ────────────────────────────────────────────────────────────────────────────
# Insider activity
# ────────────────────────────────────────────────────────────────────────────

def _insider_score(ticker: str) -> float:
    """
    Score ∈ [-1, 1].
    Positive = net buying. Negative = net selling.
    Uses Finnhub insider transactions (free tier: last 3 months).
    """
    to_date = datetime.now(tz=timezone.utc).date()
    from_date = to_date - timedelta(days=90)

    data = _finnhub_get("stock/insider-transactions", {
        "symbol": ticker,
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
    })
    if not data or not isinstance(data, dict):
        return 0.0

    transactions = data.get("data", [])
    if not transactions:
        return 0.0

    buy_shares = 0
    sell_shares = 0
    for tx in transactions:
        shares = abs(tx.get("share", 0) or 0)
        tx_type = (tx.get("transactionCode") or "").upper()
        if tx_type in ("P", "A"):       # Purchase / Award exercised and held
            buy_shares += shares
        elif tx_type in ("S", "D"):     # Sale / Disposition
            sell_shares += shares

    total = buy_shares + sell_shares
    if total == 0:
        return 0.0

    return (buy_shares - sell_shares) / total  # range [-1, 1]


# ────────────────────────────────────────────────────────────────────────────
# News sentiment
# ────────────────────────────────────────────────────────────────────────────

def _news_sentiment_score(ticker: str) -> tuple[float, list[str]]:
    """
    Returns (score ∈ [-1, 1], list_of_recent_headlines).
    Uses Finnhub company-news sentiment; falls back to raw headline count heuristic.
    """
    # Finnhub provides a sentiment endpoint
    data = _finnhub_get("news-sentiment", {"symbol": ticker})
    headlines: list[str] = []

    if data and isinstance(data, dict):
        buzz = data.get("buzz", {})
        sentiment = data.get("sentiment", {})
        # bearishPct / bullishPct from Finnhub
        bullish = sentiment.get("bullishPercent", 0.5)
        score = (bullish - 0.5) * 2  # map [0,1] → [-1,1]
        return score, headlines

    # Fallback: Finnhub company news — count positive vs negative words
    to_date = datetime.now(tz=timezone.utc).date()
    from_date = to_date - timedelta(days=7)
    news_data = _finnhub_get("company-news", {
        "symbol": ticker,
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
    })
    if news_data and isinstance(news_data, list):
        positive_words = {
            "beat", "beats", "record", "surge", "soar", "strong", "upgrade",
            "raises", "raised", "growth", "profit", "gain", "outperform",
        }
        negative_words = {
            "miss", "misses", "drop", "decline", "cut", "cuts", "loss",
            "warn", "warning", "downgrade", "below", "weak", "risk",
        }
        pos = neg = 0
        for article in news_data[:20]:
            headline = (article.get("headline") or "").lower()
            headlines.append(article.get("headline", ""))
            for w in positive_words:
                if w in headline:
                    pos += 1
            for w in negative_words:
                if w in headline:
                    neg += 1
        total = pos + neg
        if total > 0:
            return (pos - neg) / total, headlines[:5]

    return 0.0, headlines


# ────────────────────────────────────────────────────────────────────────────
# Analyst revision momentum
# ────────────────────────────────────────────────────────────────────────────

def _analyst_revision_score(ticker: str) -> float:
    """
    Score ∈ [-1, 1] based on net analyst rating changes over 90 days.
    Uses Finnhub recommendation trends.
    """
    data = _finnhub_get("stock/recommendation", {"symbol": ticker})
    if not data or not isinstance(data, list) or len(data) < 2:
        return 0.0

    # data[0] is most recent period, data[1] is prior period
    current = data[0]
    prior = data[1]

    strong_buy_delta = (current.get("strongBuy", 0) - prior.get("strongBuy", 0))
    buy_delta = (current.get("buy", 0) - prior.get("buy", 0))
    sell_delta = (current.get("sell", 0) - prior.get("sell", 0))
    strong_sell_delta = (current.get("strongSell", 0) - prior.get("strongSell", 0))

    net = (2 * strong_buy_delta + buy_delta) - (sell_delta + 2 * strong_sell_delta)
    # Normalise to [-1, 1] by assuming ±10 is a very large swing
    return max(-1.0, min(1.0, net / 10.0))


# ────────────────────────────────────────────────────────────────────────────
# Public interface
# ────────────────────────────────────────────────────────────────────────────

def fetch_catalysts(ticker: str) -> CatalystData:
    """Return a CatalystData instance with all catalyst signals populated."""
    result = CatalystData(ticker=ticker)
    window = PIPELINE_SETTINGS.earnings_window_days

    try:
        # Earnings proximity
        result.next_earnings_date, result.days_to_earnings = _fetch_next_earnings(ticker)
        result.earnings_proximity_score = _earnings_proximity_score(
            result.days_to_earnings, window
        )

        # Insider activity
        result.insider_score = _insider_score(ticker)

        # News sentiment
        result.news_sentiment_score, result.recent_news = _news_sentiment_score(ticker)

        # Analyst revisions
        result.analyst_revision_score = _analyst_revision_score(ticker)

    except Exception as exc:
        logger.warning("catalyst fetch failed for %s: %s", ticker, exc)
        result.error = str(exc)

    return result
