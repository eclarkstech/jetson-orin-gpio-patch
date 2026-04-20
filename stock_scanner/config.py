"""
config.py — Central configuration for the stock scanner pipeline.

All weights, thresholds, universe lists, and API credentials live here so
nothing is hard-coded elsewhere.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

# ---------------------------------------------------------------------------
# API credentials  (override via environment variables or a .env file)
# ---------------------------------------------------------------------------
# Free-tier Finnhub key — sign up at https://finnhub.io (no credit card)
FINNHUB_API_KEY: str = os.getenv("FINNHUB_API_KEY", "")

# Optional: Alpha Vantage (https://www.alphavantage.co/support/#api-key)
ALPHA_VANTAGE_API_KEY: str = os.getenv("ALPHA_VANTAGE_API_KEY", "")

# ---------------------------------------------------------------------------
# Default ticker universe
# Swap this list for any set of symbols you want to track.
# ---------------------------------------------------------------------------
DEFAULT_UNIVERSE: List[str] = [
    # Large-cap tech / high-quality compounders
    "AAPL", "MSFT", "GOOGL", "META", "AMZN", "NVDA", "AVGO",
    # Financials / industrials
    "JPM", "V", "MA", "UNH", "LLY",
    # Mid-cap growth
    "CRWD", "DDOG", "SNOW", "TTD", "CELH",
    # Consumer / retail
    "NKE", "COST", "SBUX",
    # Energy / materials
    "XOM", "CVX",
]

# ---------------------------------------------------------------------------
# Fundamental scoring weights  (must sum to 1.0)
# ---------------------------------------------------------------------------
FUNDAMENTAL_WEIGHTS: dict[str, float] = {
    "revenue_growth":   0.20,
    "eps_growth":       0.20,
    "fcf_growth":       0.20,
    "roic":             0.20,
    "buyback_yield":    0.20,
}

# ---------------------------------------------------------------------------
# Movement-potential layer weights  (must sum to 1.0)
# ---------------------------------------------------------------------------
MOVEMENT_WEIGHTS: dict[str, float] = {
    "relative_volume":      0.35,
    "breakout_strength":    0.35,
    "earnings_momentum":    0.30,
}

# ---------------------------------------------------------------------------
# Catalyst layer weights  (must sum to 1.0)
# ---------------------------------------------------------------------------
CATALYST_WEIGHTS: dict[str, float] = {
    "earnings_proximity":   0.30,
    "insider_activity":     0.25,
    "news_sentiment":       0.25,
    "analyst_revision":     0.20,
}

# ---------------------------------------------------------------------------
# Final composite blend weights  (must sum to 1.0)
# ---------------------------------------------------------------------------
COMPOSITE_WEIGHTS: dict[str, float] = {
    "fundamental":  0.50,
    "movement":     0.25,
    "catalyst":     0.25,
}

# ---------------------------------------------------------------------------
# Quality / risk filter thresholds
# ---------------------------------------------------------------------------
@dataclass
class QualityThresholds:
    min_price: float = 5.0           # Reject penny stocks
    min_market_cap_m: float = 500.0  # Minimum market cap ($M)
    max_debt_to_equity: float = 2.0  # D/E ratio ceiling
    min_current_ratio: float = 1.0   # Basic liquidity check
    require_positive_fcf: bool = True
    require_improving_gross_margin: bool = True
    min_gross_margin: float = 0.15   # 15 % floor

QUALITY_THRESHOLDS = QualityThresholds()

# ---------------------------------------------------------------------------
# Pipeline settings
# ---------------------------------------------------------------------------
@dataclass
class PipelineSettings:
    top_n_picks: int = 10            # How many final picks to surface
    # Earnings proximity window — flag stocks with earnings within N days
    earnings_window_days: int = 14
    # Relative-volume threshold to consider "active"
    min_relative_volume: float = 1.2
    # Breakout: stock is within this % of its 52-week high
    breakout_proximity_pct: float = 0.10
    # Cache TTL in seconds (avoids hammering APIs during development)
    cache_ttl_seconds: int = 3600

PIPELINE_SETTINGS = PipelineSettings()

# ---------------------------------------------------------------------------
# Notification settings
# ---------------------------------------------------------------------------
@dataclass
class NotificationSettings:
    # Set to an email address to receive picks via SMTP
    email_to: str = os.getenv("NOTIFY_EMAIL_TO", "")
    email_from: str = os.getenv("NOTIFY_EMAIL_FROM", "")
    smtp_host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    # Slack webhook URL (https://api.slack.com/messaging/webhooks)
    slack_webhook_url: str = os.getenv("SLACK_WEBHOOK_URL", "")

NOTIFICATION_SETTINGS = NotificationSettings()
