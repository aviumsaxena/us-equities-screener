import argparse

from etl.pipeline import run
from etl.tickers import SAMPLE_TICKERS

parser = argparse.ArgumentParser(description="Run the ETL pipeline (bronze -> silver -> gold)")
parser.add_argument("--sample", action="store_true", help="run against the sample ticker universe")
parser.add_argument("--tickers", nargs="+", help="explicit tickers, overrides --sample")
args = parser.parse_args()

run(args.tickers or SAMPLE_TICKERS)
