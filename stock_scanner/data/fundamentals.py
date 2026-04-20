"""
data/fundamentals.py — Fetch and compute fundamental metrics via yfinance.

Metrics returned per ticker
────────────────────────────
revenue_growth      YoY total-revenue growth (TTM vs prior year)
eps_growth          YoY diluted-EPS growth
fcf_growth          YoY free-cash-flow growth
roic                Return on Invested Capital (NOPAT / Invested Capital)
buyback_yield       Net share-count reduction as % of shares (positive = buyback)
trailing_fcf        Trailing-12-month FCF in absolute dollars
debt_to_equity      Total debt / total equity
current_ratio       Current assets / current liabilities
gross_margin        Gross profit / revenue (most recent annual)
prev_gross_margin   Gross profit / revenue (prior year)
market_cap          Market capitalisation in dollars
price               Last closing price
shares_outstanding  Most recent diluted share count
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)


@dataclass
class FundamentalData:
    ticker: str
    revenue_growth: Optional[float] = None
    eps_growth: Optional[float] = None
    fcf_growth: Optional[float] = None
    roic: Optional[float] = None
    buyback_yield: Optional[float] = None
    trailing_fcf: Optional[float] = None
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    gross_margin: Optional[float] = None
    prev_gross_margin: Optional[float] = None
    market_cap: Optional[float] = None
    price: Optional[float] = None
    shares_outstanding: Optional[float] = None
    error: Optional[str] = None


def _safe_pct_change(current: Optional[float], prior: Optional[float]) -> Optional[float]:
    """Return (current - prior) / |prior|, or None if inputs are invalid."""
    if current is None or prior is None:
        return None
    if prior == 0:
        return None
    return (current - prior) / abs(prior)


def _get_income_stmt_row(income_stmt, row_name: str):
    """Return (current_year_value, prior_year_value) for an income-statement row."""
    if income_stmt is None or income_stmt.empty:
        return None, None
    if row_name not in income_stmt.index:
        return None, None
    row = income_stmt.loc[row_name]
    # Columns are sorted newest → oldest
    values = row.dropna().tolist()
    current = values[0] if len(values) > 0 else None
    prior = values[1] if len(values) > 1 else None
    return current, prior


def _get_cashflow_row(cashflow, row_name: str):
    """Return (current_year_value, prior_year_value) from the cashflow statement."""
    if cashflow is None or cashflow.empty:
        return None, None
    if row_name not in cashflow.index:
        return None, None
    row = cashflow.loc[row_name]
    values = row.dropna().tolist()
    current = values[0] if len(values) > 0 else None
    prior = values[1] if len(values) > 1 else None
    return current, prior


def _compute_roic(income_stmt, balance_sheet) -> Optional[float]:
    """
    ROIC = NOPAT / Invested Capital
    NOPAT  ≈ EBIT × (1 − effective_tax_rate)
    Invested Capital = Total Assets − Current Liabilities − Excess Cash
    """
    if income_stmt is None or income_stmt.empty:
        return None
    if balance_sheet is None or balance_sheet.empty:
        return None

    def _row(df, name):
        if name in df.index:
            vals = df.loc[name].dropna().tolist()
            return vals[0] if vals else None
        return None

    ebit = _row(income_stmt, "EBIT")
    if ebit is None:
        # Fallback: Operating Income
        ebit = _row(income_stmt, "Operating Income")
    if ebit is None:
        return None

    pretax = _row(income_stmt, "Pretax Income")
    tax = _row(income_stmt, "Tax Provision")
    if pretax and tax and pretax != 0:
        tax_rate = tax / pretax
        # Clamp to realistic range
        tax_rate = max(0.0, min(tax_rate, 0.40))
    else:
        tax_rate = 0.21  # default US corporate rate

    nopat = ebit * (1 - tax_rate)

    total_assets = _row(balance_sheet, "Total Assets")
    current_liabilities = _row(balance_sheet, "Current Liabilities")
    cash = _row(balance_sheet, "Cash And Cash Equivalents") or 0.0

    if total_assets is None or current_liabilities is None:
        return None

    invested_capital = total_assets - current_liabilities - cash
    if invested_capital <= 0:
        return None

    return nopat / invested_capital


def _compute_buyback_yield(balance_sheet, cashflow) -> Optional[float]:
    """
    Net buyback yield = (prior shares − current shares) / prior shares.
    Positive value → company is reducing share count (good).
    Uses 'Common Stock Shares Outstanding' from balance sheet when available,
    falling back to 'Repurchase Of Capital Stock' from the cash flow statement.
    """
    if balance_sheet is not None and not balance_sheet.empty:
        for label in (
            "Common Stock Shares Outstanding",
            "Ordinary Shares Number",
            "Share Issued",
        ):
            if label in balance_sheet.index:
                row = balance_sheet.loc[label].dropna().tolist()
                if len(row) >= 2:
                    current_shares, prior_shares = row[0], row[1]
                    if prior_shares and prior_shares != 0:
                        return (prior_shares - current_shares) / abs(prior_shares)

    # Fallback: net share repurchase from cash flow
    if cashflow is not None and not cashflow.empty:
        repurchase = None
        issuance = None
        for label in ("Repurchase Of Capital Stock", "Common Stock Repurchased"):
            if label in cashflow.index:
                vals = cashflow.loc[label].dropna().tolist()
                repurchase = vals[0] if vals else None
                break
        for label in ("Common Stock Issued", "Proceeds From Issuance Of Common Stock"):
            if label in cashflow.index:
                vals = cashflow.loc[label].dropna().tolist()
                issuance = vals[0] if vals else None
                break

        # yfinance reports repurchases as negative; normalise
        repurchase = -(repurchase or 0)
        issuance = issuance or 0
        net_buyback = repurchase - issuance

        # We need market cap to express as a yield — if not available, skip
        # (caller can normalise later)
        return net_buyback  # raw dollar amount; scorer normalises

    return None


def fetch_fundamentals(ticker: str) -> FundamentalData:
    """Return a FundamentalData instance populated from yfinance."""
    result = FundamentalData(ticker=ticker)
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}

        # ── Price & market cap ───────────────────────────────────────────
        result.price = info.get("currentPrice") or info.get("regularMarketPrice")
        result.market_cap = info.get("marketCap")
        result.shares_outstanding = info.get("sharesOutstanding")

        # ── Financial statements ─────────────────────────────────────────
        income_stmt = t.financials          # annual income statement
        balance_sheet = t.balance_sheet     # annual balance sheet
        cashflow = t.cashflow               # annual cash flow

        # ── Revenue growth ───────────────────────────────────────────────
        cur_rev, pri_rev = _get_income_stmt_row(income_stmt, "Total Revenue")
        result.revenue_growth = _safe_pct_change(cur_rev, pri_rev)

        # ── EPS growth ───────────────────────────────────────────────────
        # Prefer reported diluted EPS from info; fallback to net-income / shares
        eps_ttm = info.get("trailingEps")
        eps_fwd = info.get("forwardEps")
        if eps_ttm and eps_fwd and eps_ttm != 0:
            # Forward vs trailing as a proxy for YoY growth direction
            result.eps_growth = (eps_fwd - eps_ttm) / abs(eps_ttm)
        else:
            cur_ni, pri_ni = _get_income_stmt_row(income_stmt, "Net Income")
            result.eps_growth = _safe_pct_change(cur_ni, pri_ni)

        # ── FCF growth ───────────────────────────────────────────────────
        cur_cfo, pri_cfo = _get_cashflow_row(cashflow, "Operating Cash Flow")
        cur_cap, pri_cap = _get_cashflow_row(cashflow, "Capital Expenditure")
        # capex is usually negative in yfinance
        if cur_cfo is not None and cur_cap is not None:
            cur_fcf = cur_cfo + cur_cap  # capex is negative, so this subtracts
        else:
            cur_fcf = None
        if pri_cfo is not None and pri_cap is not None:
            pri_fcf = pri_cfo + pri_cap
        else:
            pri_fcf = None

        result.trailing_fcf = cur_fcf
        result.fcf_growth = _safe_pct_change(cur_fcf, pri_fcf)

        # ── ROIC ─────────────────────────────────────────────────────────
        result.roic = _compute_roic(income_stmt, balance_sheet)

        # ── Buyback yield ────────────────────────────────────────────────
        raw_buyback = _compute_buyback_yield(balance_sheet, cashflow)
        if raw_buyback is not None:
            # If we got a ratio already, use it; if it looks like a dollar amount normalise
            if abs(raw_buyback) > 1:
                # Dollar amount — normalise by market cap
                mkt = result.market_cap or 0
                result.buyback_yield = raw_buyback / mkt if mkt else None
            else:
                result.buyback_yield = raw_buyback
        # ── Balance-sheet risk metrics ───────────────────────────────────
        result.debt_to_equity = info.get("debtToEquity")
        if result.debt_to_equity is not None:
            # yfinance returns D/E × 100 for some tickers; normalise
            if result.debt_to_equity > 20:
                result.debt_to_equity /= 100.0

        result.current_ratio = info.get("currentRatio")

        # ── Gross margin (current and prior year) ────────────────────────
        cur_gp, pri_gp = _get_income_stmt_row(income_stmt, "Gross Profit")
        if cur_gp is not None and cur_rev and cur_rev != 0:
            result.gross_margin = cur_gp / cur_rev
        if pri_gp is not None and pri_rev and pri_rev != 0:
            result.prev_gross_margin = pri_gp / pri_rev

    except Exception as exc:
        logger.warning("fundamentals fetch failed for %s: %s", ticker, exc)
        result.error = str(exc)

    return result
