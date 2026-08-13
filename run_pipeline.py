"""
Convenience wrapper: validate raw data -> build the processed Parquet
dataset -> build derived features (VWAP).

    python run_pipeline.py --symbol RELIND [--interval 1m]
                            [--holidays data/nse_holidays.json]
                            [--force]         # build even if validation found CRITICAL issues
                            [--skip-features]  # stop after the OHLCV dataset, skip VWAP etc.

This never modifies data/raw/ (validation is read-only) or
data/processed/parquet/ (features write to their own tree under
data/processed/features/).
"""

import argparse
import sys

import config
import validate_raw_data as validate
import build_processed_dataset as build
from features import vwap


def main():
    parser = argparse.ArgumentParser(description="Validate + build the processed dataset for a symbol.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", default=config.INTERVAL_DIR)
    parser.add_argument("--holidays", default=None)
    parser.add_argument("--force", action="store_true",
                         help="Build the Parquet dataset even if validation found CRITICAL issues")
    parser.add_argument("--skip-features", action="store_true",
                         help="Stop after the OHLCV dataset; don't build derived features (VWAP)")
    args = parser.parse_args()

    report_path = f"{config.DATA_ROOT}/validation_report_{args.symbol}.json"
    validation_exit_code = validate.run_validation(
        args.symbol, args.interval, args.holidays, report_path
    )

    if validation_exit_code != 0 and not args.force:
        print()
        print("Validation found CRITICAL issues - stopping before building the "
              "processed dataset. Review the report above/at "
              f"{report_path}, fix or accept the data, then re-run "
              "(or pass --force to build anyway).")
        sys.exit(validation_exit_code)

    print()
    build_exit_code = build.run(args.symbol, args.interval)
    if build_exit_code != 0 or args.skip_features:
        sys.exit(build_exit_code)

    print()
    feature_exit_code = vwap.run(args.symbol, args.interval)
    sys.exit(feature_exit_code)


if __name__ == "__main__":
    main()
