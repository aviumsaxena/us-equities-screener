import argparse

from etl.pipeline import DAILY_PRICE_WINDOW, run
from etl.tickers import SAMPLE_TICKERS

parser = argparse.ArgumentParser(description="Run the ETL pipeline (bronze -> silver -> gold)")
parser.add_argument(
    "--sample", action="store_true", help="run against the 20-ticker sample universe"
)
parser.add_argument("--tickers", nargs="+", help="explicit tickers, overrides --sample")
parser.add_argument(
    "--price-days",
    type=int,
    default=DAILY_PRICE_WINDOW,
    help="trading days of prices to pull (bronze days are replayed free)",
)
args = parser.parse_args()

if args.tickers:
    universe = args.tickers
elif args.sample:
    universe = SAMPLE_TICKERS
else:
    universe = None  # the full SEC universe

run(universe, price_days=args.price_days)
