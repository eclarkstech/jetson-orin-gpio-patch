#!/usr/bin/env python3
"""
main.py — CLI entry point for the stock scanner pipeline.

Usage
─────
    # Run with the default universe, print top 10 picks
    python -m stock_scanner.main

    # Evaluate a custom list of tickers
    python -m stock_scanner.main --tickers AAPL MSFT NVDA

    # Override top-N
    python -m stock_scanner.main --top 5

    # Evaluate a single ticker (no quality filter, full debug output)
    python -m stock_scanner.main --single NVDA

    # Use a universe file (one ticker per line)
    python -m stock_scanner.main --universe-file my_tickers.txt

Scheduling
──────────
    Add to cron for daily refresh at market close (4:30 PM ET):
        30 16 * * 1-5  /usr/bin/python3 /path/to/main.py

    Or weekly on Monday morning:
        0 8 * * 1  /usr/bin/python3 /path/to/main.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from stock_scanner.pipeline.watchlist import WatchlistPipeline
from stock_scanner.pipeline.notifier import Notifier, build_rationale
from stock_scanner.config import DEFAULT_UNIVERSE, PIPELINE_SETTINGS


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stock scanner — fundamental + catalyst watchlist pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        metavar="TICKER",
        help="Space-separated list of tickers to evaluate (overrides default universe)",
    )
    parser.add_argument(
        "--universe-file",
        metavar="FILE",
        help="Path to a text file with one ticker per line",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=PIPELINE_SETTINGS.top_n_picks,
        metavar="N",
        help=f"Number of top picks to surface (default: {PIPELINE_SETTINGS.top_n_picks})",
    )
    parser.add_argument(
        "--single",
        metavar="TICKER",
        help="Evaluate a single ticker and print a detailed report",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        metavar="N",
        help="Thread-pool size for concurrent data fetching (default: 8)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG-level logging",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Print report to stdout only; skip email/Slack dispatch",
    )
    return parser.parse_args(argv)


def _build_universe(args: argparse.Namespace) -> list[str]:
    if args.tickers:
        return [t.upper().strip() for t in args.tickers]
    if args.universe_file:
        path = Path(args.universe_file)
        if not path.exists():
            print(f"ERROR: universe file not found: {path}", file=sys.stderr)
            sys.exit(1)
        tickers = [line.strip().upper() for line in path.read_text().splitlines() if line.strip()]
        if not tickers:
            print("ERROR: universe file is empty.", file=sys.stderr)
            sys.exit(1)
        return tickers
    return DEFAULT_UNIVERSE


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Single-ticker mode ────────────────────────────────────────────────
    if args.single:
        ticker = args.single.upper().strip()
        print(f"\nEvaluating {ticker} …\n")
        pipeline = WatchlistPipeline(universe=[ticker], top_n=1)
        entry = pipeline.run_single(ticker)
        print(build_rationale(entry))

        if entry.fund_score_detail:
            print("\nFundamental components:")
            for k, v in entry.fund_score_detail.components.items():
                raw = entry.fund_score_detail.raw_metrics.get(k)
                raw_str = f"{raw:.4f}" if raw is not None else "N/A"
                print(f"  {k:<20s}  raw={raw_str:<10s}  normalised={v:.3f}")

        if entry.filter_result and not entry.filter_result.passed:
            print("\n⚠  Quality filter FAILED:")
            for reason in entry.filter_result.reasons:
                print(f"   • {reason}")
        return

    # ── Full pipeline mode ────────────────────────────────────────────────
    universe = _build_universe(args)
    pipeline = WatchlistPipeline(
        universe=universe,
        top_n=args.top,
        max_workers=args.workers,
    )
    picks = pipeline.run()

    if not picks:
        print("\nNo tickers passed the quality filter. Try widening thresholds in config.py.")
        return

    if args.no_notify:
        from stock_scanner.pipeline.notifier import build_rationale
        from datetime import datetime, timezone
        now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M %Z")
        header = f"\n{'═'*60}\n  📈  TOP STOCK PICKS  —  {now}\n{'═'*60}\n"
        print(header + "\n\n".join(build_rationale(e) for e in picks))
    else:
        notifier = Notifier()
        notifier.notify(picks)


if __name__ == "__main__":
    main()
