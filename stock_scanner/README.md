# Stock Scanner — Automated Watchlist Pipeline

An automated pipeline that scores, filters, and ranks stocks using a
multi-layer model combining fundamentals, technicals, and news catalysts —
then delivers concise, actionable pick briefs to your inbox or Slack.

---

## Architecture Overview

```
stock_scanner/
├── config.py              ← Universe, weights, thresholds, credentials
├── main.py                ← CLI entry point
│
├── data/
│   ├── fundamentals.py    ← Revenue / EPS / FCF growth, ROIC, buybacks  (yfinance)
│   ├── technicals.py      ← Relative volume, breakout strength, MAs      (yfinance)
│   └── catalysts.py       ← Earnings dates, insider activity, news        (Finnhub)
│
├── scoring/
│   ├── quality_filter.py  ← Hard gates: FCF ≥ 0, D/E ceiling, margin trend
│   ├── scorer.py          ← Weighted fundamental score  [0, 1]
│   └── movement.py        ← Movement-potential layer    [0, 1]
│
└── pipeline/
    ├── watchlist.py       ← Orchestrates full refresh + ranking
    └── notifier.py        ← Builds rationale briefs; dispatches email / Slack
```

---

## Scoring Model

### Layer 1 — Quality / Risk Filters (hard gates, applied first)

A ticker is **excluded** before scoring if any of these fail:

| Gate | Default threshold |
|---|---|
| Minimum price | \$5.00 |
| Minimum market cap | \$500 M |
| Positive trailing FCF | Required |
| Debt / Equity | ≤ 2.0 |
| Current ratio | ≥ 1.0 |
| Gross margin | ≥ 15 % |
| Gross margin trend | Must be stable or improving |

All thresholds are configurable in `config.py → QualityThresholds`.

---

### Layer 2 — Fundamental Score (50% of composite)

Five metrics, each normalised via a sigmoid to [0, 1]:

| Metric | Weight | What it measures |
|---|---|---|
| Revenue growth (YoY) | 20 % | Top-line compounding |
| EPS growth (YoY) | 20 % | Earnings power |
| FCF growth (YoY) | 20 % | Cash conversion quality |
| ROIC | 20 % | Capital allocation efficiency |
| Net buyback yield | 20 % | Share count discipline |

---

### Layer 3 — Movement Potential (25% of composite)

| Signal | Weight | Rationale |
|---|---|---|
| Relative volume | 35 % | Unusual activity signals institutional interest |
| Breakout strength | 35 % | Proximity to 52-week high = price momentum |
| Earnings momentum | 30 % | Earnings proximity + analyst revision trend |

---

### Layer 4 — Catalyst Score (25% of composite)

| Signal | Weight | Source |
|---|---|---|
| Earnings proximity | 30 % | Finnhub / yfinance calendar |
| Insider activity | 25 % | Finnhub insider transactions |
| News sentiment | 25 % | Finnhub news-sentiment API |
| Analyst revisions | 20 % | Finnhub recommendation trends |

---

### Final Composite

```
composite = 0.50 × fundamental + 0.25 × movement + 0.25 × catalyst
```

All weights are editable in `config.py`.

---

## Sample Pick Brief

```
════════════════════════════════════════════════════════════
  📈  TOP STOCK PICKS  —  2026-04-20 16:32 UTC
════════════════════════════════════════════════════════════

────────────────────────────────────────────────────────────
  NVDA  |  Price: $873.40  |  Composite: 0.812
         (F:0.89  M:0.78  C:0.74)
────────────────────────────────────────────────────────────
  What changed   : Revenue grew 122.4% YoY.
  Why now        : Earnings in 8 days (2026-05-28) — elevated event-driven potential.
  Upside driver  : High-return capital allocator (ROIC 38.2%); elevated volume (2.3× average).
  Downside risk  : Binary earnings event approaching — gap risk.
```

---

## Setup

### 1. Install dependencies

```bash
cd stock_scanner
pip install -r requirements.txt
```

### 2. Configure API keys (optional but recommended)

The pipeline works out of the box using only **yfinance** (no key required).
For richer catalyst data, set these environment variables:

| Variable | Where to get it | Used for |
|---|---|---|
| `FINNHUB_API_KEY` | [finnhub.io](https://finnhub.io) — free tier | Earnings calendar, insider activity, news sentiment, analyst revisions |
| `ALPHA_VANTAGE_API_KEY` | [alphavantage.co](https://www.alphavantage.co) — free tier | Reserved for future fundamental enrichment |

```bash
export FINNHUB_API_KEY="your_key_here"
```

### 3. Configure notifications (optional)

**Email (Gmail example):**
```bash
export NOTIFY_EMAIL_TO="you@example.com"
export NOTIFY_EMAIL_FROM="sender@gmail.com"
export SMTP_PASSWORD="your_app_password"
```

**Slack:**
```bash
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
```

---

## Running the Pipeline

```bash
# Run with default universe (25 large/mid-cap names), top 10 picks
python -m stock_scanner.main

# Custom universe
python -m stock_scanner.main --tickers AAPL MSFT NVDA CRWD

# Universe from file (one ticker per line)
python -m stock_scanner.main --universe-file my_watchlist.txt

# Top 5 picks only
python -m stock_scanner.main --top 5

# Deep-dive on a single ticker
python -m stock_scanner.main --single NVDA

# Skip email/Slack, stdout only
python -m stock_scanner.main --no-notify

# Verbose logging
python -m stock_scanner.main --verbose
```

---

## Scheduling (Daily / Weekly Refresh)

### cron — daily at 4:30 PM Eastern (market close)

```cron
30 16 * * 1-5  /usr/bin/python3 /path/to/stock_scanner/main.py
```

### cron — weekly on Monday morning

```cron
0 8 * * 1  /usr/bin/python3 /path/to/stock_scanner/main.py
```

### GitHub Actions (weekly on Monday)

Create `.github/workflows/stock_scan.yml`:

```yaml
name: Weekly Stock Scan
on:
  schedule:
    - cron: '0 13 * * 1'   # Mondays 1:00 PM UTC = 9 AM ET
  workflow_dispatch:

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r stock_scanner/requirements.txt
      - run: python -m stock_scanner.main --no-notify
        env:
          FINNHUB_API_KEY: ${{ secrets.FINNHUB_API_KEY }}
```

---

## Customisation

### Change the universe

Edit `DEFAULT_UNIVERSE` in `config.py`, or pass `--tickers` on the CLI.

### Adjust scoring weights

All weight dictionaries are in `config.py`:

```python
FUNDAMENTAL_WEIGHTS = {"revenue_growth": 0.20, "eps_growth": 0.20, ...}
MOVEMENT_WEIGHTS    = {"relative_volume": 0.35, ...}
CATALYST_WEIGHTS    = {"earnings_proximity": 0.30, ...}
COMPOSITE_WEIGHTS   = {"fundamental": 0.50, "movement": 0.25, "catalyst": 0.25}
```

### Tighten / loosen quality gates

```python
QUALITY_THRESHOLDS = QualityThresholds(
    min_price=10.0,
    max_debt_to_equity=1.5,
    require_positive_fcf=True,
    require_improving_gross_margin=False,   # turn off if you want turnarounds
)
```

### Add more tickers or sectors

Use `--universe-file` with any list of valid Yahoo Finance tickers,
including ETFs, ADRs, and international symbols (e.g., `ASML`, `TSM`, `SHOP`).

---

## Data Sources

| Source | What it provides | Cost |
|---|---|---|
| [yfinance](https://github.com/ranaroussi/yfinance) | Price history, income statement, balance sheet, cash flow | Free (scrapes Yahoo Finance) |
| [Finnhub](https://finnhub.io) | Earnings calendar, insider transactions, news sentiment, analyst ratings | Free tier: 60 calls/min |
| SEC EDGAR | Authoritative share counts (future enrichment) | Free |

---

## Disclaimer

This tool is for **informational and educational purposes only**.
It does not constitute financial advice. Always conduct your own research
before making investment decisions.
