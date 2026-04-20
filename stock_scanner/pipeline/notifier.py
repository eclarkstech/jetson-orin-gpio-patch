"""
pipeline/notifier.py — Format top-pick rationales and dispatch notifications.

Each pick is rendered as a short brief:
  • What changed   — biggest YoY improvement in the fundamental data
  • Why now        — top catalyst signal
  • Key upside driver
  • Key downside risk

Notification channels supported:
  • Console / stdout  (always on)
  • Email via SMTP    (requires NOTIFY_EMAIL_TO and SMTP_PASSWORD env vars)
  • Slack webhook     (requires SLACK_WEBHOOK_URL env var)
"""

from __future__ import annotations

import logging
import smtplib
import textwrap
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

import requests

from stock_scanner.config import NOTIFICATION_SETTINGS, PIPELINE_SETTINGS
from stock_scanner.pipeline.watchlist import WatchlistEntry

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# Rationale builder
# ────────────────────────────────────────────────────────────────────────────

def _pct(v: Optional[float], default: str = "N/A") -> str:
    return f"{v:.1%}" if v is not None else default


def _round2(v: Optional[float], default: str = "N/A") -> str:
    return f"{v:.2f}" if v is not None else default


def _what_changed(entry: WatchlistEntry) -> str:
    """Identify the single largest positive driver in fundamental data."""
    fd = entry.fundamentals
    if fd is None:
        return "Fundamental data unavailable."

    candidates: list[tuple[float, str]] = []

    if fd.revenue_growth is not None:
        candidates.append((fd.revenue_growth, f"Revenue grew {_pct(fd.revenue_growth)} YoY"))
    if fd.eps_growth is not None:
        candidates.append((fd.eps_growth, f"EPS grew {_pct(fd.eps_growth)} YoY"))
    if fd.fcf_growth is not None:
        candidates.append((fd.fcf_growth, f"Free cash flow grew {_pct(fd.fcf_growth)} YoY"))
    if fd.roic is not None:
        candidates.append((fd.roic, f"ROIC of {_pct(fd.roic)}"))
    if fd.buyback_yield is not None and fd.buyback_yield > 0:
        candidates.append((fd.buyback_yield, f"Net buyback yield {_pct(fd.buyback_yield)}"))

    if not candidates:
        return "No significant fundamental change identified."

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1] + "."


def _why_now(entry: WatchlistEntry) -> str:
    """Surface the most timely catalyst."""
    cat = entry.catalysts
    if cat is None:
        return "No catalyst data available."

    signals: list[tuple[float, str]] = []

    if cat.earnings_proximity_score > 0:
        days = cat.days_to_earnings
        signals.append((
            cat.earnings_proximity_score,
            f"Earnings in {days} day{'s' if days != 1 else ''} "
            f"({cat.next_earnings_date}) — elevated event-driven potential.",
        ))

    if cat.insider_score > 0.2:
        signals.append((
            cat.insider_score,
            "Net insider buying detected in the past 90 days.",
        ))

    if cat.news_sentiment_score > 0.2:
        signals.append((
            cat.news_sentiment_score,
            "Positive recent news flow / favourable sentiment.",
        ))

    if cat.analyst_revision_score > 0.1:
        signals.append((
            cat.analyst_revision_score,
            "Analyst upgrades or positive EPS revisions in recent weeks.",
        ))

    if not signals:
        return "No outstanding near-term catalyst identified."

    signals.sort(key=lambda x: x[0], reverse=True)
    return signals[0][1]


def _upside_driver(entry: WatchlistEntry) -> str:
    """One-line upside thesis."""
    fd = entry.fundamentals
    td = entry.technicals

    parts: list[str] = []

    if fd and fd.roic is not None and fd.roic > 0.15:
        parts.append(f"high-return capital allocator (ROIC {_pct(fd.roic)})")

    if td and td.relative_volume is not None and td.relative_volume > PIPELINE_SETTINGS.min_relative_volume:
        parts.append(f"elevated volume ({td.relative_volume:.1f}× average)")

    if td and td.breakout_strength is not None and td.breakout_strength > 0.85:
        parts.append("trading near 52-week highs (breakout candidate)")

    if fd and fd.revenue_growth is not None and fd.revenue_growth > 0.20:
        parts.append(f"strong revenue momentum ({_pct(fd.revenue_growth)} YoY)")

    if not parts:
        return "Improving fundamentals with positive free cash flow."

    return "; ".join(parts).capitalize() + "."


def _downside_risk(entry: WatchlistEntry) -> str:
    """One-line downside risk."""
    fd = entry.fundamentals
    cat = entry.catalysts

    risks: list[str] = []

    if fd and fd.debt_to_equity is not None and fd.debt_to_equity > 1.0:
        risks.append(f"elevated leverage (D/E {_round2(fd.debt_to_equity)})")

    if cat and cat.days_to_earnings is not None and cat.days_to_earnings <= 14:
        risks.append("binary earnings event approaching — gap risk")

    if fd and fd.gross_margin is not None and fd.prev_gross_margin is not None:
        if fd.gross_margin < fd.prev_gross_margin:
            risks.append(
                f"margin compression "
                f"({_pct(fd.prev_gross_margin)} → {_pct(fd.gross_margin)})"
            )

    if not risks:
        return "Broad market sell-off or valuation re-rating if growth decelerates."

    return "; ".join(risks).capitalize() + "."


def build_rationale(entry: WatchlistEntry) -> str:
    """Build the full short-form pick brief for one ticker."""
    fd = entry.fundamentals
    price_str = f"${fd.price:.2f}" if (fd and fd.price) else "N/A"

    lines = [
        f"{'─' * 60}",
        f"  {entry.ticker}  |  Price: {price_str}  |  "
        f"Composite: {entry.composite_score:.3f}  "
        f"(F:{entry.fundamental_score:.2f}  M:{entry.movement_score:.2f}  C:{entry.catalyst_score:.2f})",
        f"{'─' * 60}",
        f"  What changed   : {_what_changed(entry)}",
        f"  Why now        : {_why_now(entry)}",
        f"  Upside driver  : {_upside_driver(entry)}",
        f"  Downside risk  : {_downside_risk(entry)}",
    ]

    # Append top news headlines if available
    if entry.catalysts and entry.catalysts.recent_news:
        lines.append("  Recent news    :")
        for headline in entry.catalysts.recent_news[:3]:
            wrapped = textwrap.fill(headline, width=70, initial_indent=" " * 20, subsequent_indent=" " * 20)
            lines.append(wrapped)

    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────
# Notification channels
# ────────────────────────────────────────────────────────────────────────────

def _send_email(subject: str, body: str) -> None:
    cfg = NOTIFICATION_SETTINGS
    if not (cfg.email_to and cfg.email_from and cfg.smtp_password):
        logger.debug("Email notification skipped — credentials not configured.")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = cfg.email_from
        msg["To"] = cfg.email_to
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg.email_from, cfg.smtp_password)
            server.sendmail(cfg.email_from, cfg.email_to, msg.as_string())
        logger.info("Email notification sent to %s", cfg.email_to)
    except Exception as exc:
        logger.error("Email notification failed: %s", exc)


def _send_slack(text: str) -> None:
    url = NOTIFICATION_SETTINGS.slack_webhook_url
    if not url:
        logger.debug("Slack notification skipped — webhook not configured.")
        return
    try:
        resp = requests.post(
            url,
            json={"text": text},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Slack notification sent.")
    except Exception as exc:
        logger.error("Slack notification failed: %s", exc)


# ────────────────────────────────────────────────────────────────────────────
# Public interface
# ────────────────────────────────────────────────────────────────────────────

class Notifier:
    """
    Build pick rationales and dispatch them to configured notification channels.
    """

    def notify(self, picks: List[WatchlistEntry]) -> str:
        """
        Format all picks, print to stdout, and dispatch to email/Slack if configured.

        Returns the full formatted report as a string (useful for testing / logging).
        """
        now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M %Z")
        header = (
            f"\n{'═' * 60}\n"
            f"  📈  TOP STOCK PICKS  —  {now}\n"
            f"{'═' * 60}\n"
        )
        sections = [build_rationale(e) for e in picks]
        report = header + "\n\n".join(sections) + f"\n{'═' * 60}\n"

        # Always print
        print(report)

        # Optional channels
        subject = f"Stock Watchlist — Top {len(picks)} Picks  ({now})"
        _send_email(subject, report)
        _send_slack(report)

        return report
