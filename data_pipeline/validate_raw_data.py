"""
Validate raw Breeze JSON data before it is ever converted or touched.

This script is READ-ONLY with respect to data/raw/. It never modifies,
moves, or deletes anything under the raw directory.

Usage:

    python data_pipeline/validate_raw_data.py --symbol RELIND [--interval 1m]
                                 [--holidays data/nse_holidays.json]
                                 [--report data/validation_report_RELIND.json]

Checks performed:
  1. No missing trading days   (weekday with no file, and not a known holiday)
  2. No duplicate timestamps   (within a day's candle list)
  3. Candle count per day      (roughly 375 for a full NSE session)
  4. Timestamps                (parseable, monotonic, IST-consistent, aligned
                                 to the market session)
  5. OHLC sanity                (positive, high >= max(o,c,l), low <= min(o,c,h))
  6. Volume                     (not null; flags days with excessive zero volume)

Exit code is non-zero if any CRITICAL issue is found. WARNING-level issues
are reported but do not fail the run - they're things a human should glance
at (e.g. a suspected holiday, an illiquid day with lots of zero-volume bars).
"""

import argparse
import glob
import json
import os
import sys
from datetime import date, datetime, timedelta

import config

try:
    import pandas as pd
except ImportError:
    pd = None


# ============================================================
# DISCOVERY
# ============================================================

def find_symbol_dir(symbol, interval):
    return os.path.join(config.RAW_ROOT, symbol, interval)


def discover_day_files(symbol, interval):
    """
    Return a sorted list of (date, file_path) for every daily JSON file
    on disk for this symbol/interval, regardless of validity.
    """
    symbol_dir = find_symbol_dir(symbol, interval)

    pattern = os.path.join(symbol_dir, "*", "*", "*.json")
    files = [f for f in glob.glob(pattern) if not f.endswith(".tmp")]

    day_files = []
    for f in files:
        filename = os.path.basename(f)
        date_str = filename.replace(".json", "")
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        day_files.append((day, f))

    day_files.sort(key=lambda x: x[0])
    return day_files


# ============================================================
# HOLIDAY CALENDAR (OPTIONAL)
# ============================================================

def load_holidays(path):
    """
    Optional JSON file: a flat list of "YYYY-MM-DD" strings for known
    NSE holidays. Without this, weekdays with no file are flagged as
    WARNING "missing_trading_day" rather than assumed to be holidays,
    since we can't distinguish the two automatically.
    """
    if not path or not os.path.exists(path):
        return set()

    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    return {datetime.strptime(d, "%Y-%m-%d").date() for d in raw}


def is_weekend(d):
    return d.weekday() >= 5


# ============================================================
# PER-DAY VALIDATION
# ============================================================

def parse_candle_dt(value):
    """Parse Breeze's naive 'YYYY-MM-DD HH:MM:SS' candle timestamp string."""
    return datetime.strptime(str(value).strip(), "%Y-%m-%d %H:%M:%S")


def validate_day_file(day, file_path, issues):
    """
    Validate a single daily JSON file. Appends dicts to `issues`:
        {"severity": "CRITICAL"|"WARNING", "date": ..., "check": ..., "detail": ...}
    Returns the number of usable candles found (0 if the file is unusable).
    """

    def add(severity, check, detail):
        issues.append({
            "severity": severity,
            "date": day.isoformat(),
            "file": file_path,
            "check": check,
            "detail": detail,
        })

    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception as error:
        add("CRITICAL", "unreadable_file", str(error))
        return 0

    if not isinstance(payload, dict):
        add("CRITICAL", "malformed_file", "Top-level JSON is not an object")
        return 0

    if payload.get("status") != "data":
        add("WARNING", "empty_or_incomplete_day", f"status={payload.get('status')!r}")
        return 0

    candles = payload.get("data")
    if not isinstance(candles, list) or len(candles) == 0:
        add("CRITICAL", "no_candles", "status='data' but 'data' list is empty/missing")
        return 0

    # --------------------------------------------------------
    # 1. Duplicate timestamps
    # --------------------------------------------------------
    raw_timestamps = [c.get("datetime") for c in candles]
    seen = set()
    duplicates = set()
    for ts in raw_timestamps:
        if ts in seen:
            duplicates.add(ts)
        seen.add(ts)
    if duplicates:
        add("CRITICAL", "duplicate_timestamps",
            f"{len(duplicates)} duplicate timestamp(s), e.g. {sorted(duplicates)[:3]}")

    # --------------------------------------------------------
    # 2. Parse timestamps / IST-consistency / ordering
    # --------------------------------------------------------
    parsed = []
    unparseable = 0
    for c in candles:
        ts = c.get("datetime")
        if not ts:
            unparseable += 1
            continue
        try:
            parsed.append(parse_candle_dt(ts))
        except ValueError:
            unparseable += 1

    if unparseable:
        add("CRITICAL", "unparseable_timestamps", f"{unparseable} candle(s) with bad/missing 'datetime'")

    if parsed:
        # All candles for a given file should belong to the file's own date.
        wrong_date = [dt for dt in parsed if dt.date() != day]
        if wrong_date:
            add("CRITICAL", "timestamp_date_mismatch",
                f"{len(wrong_date)} candle(s) whose date != filename date ({day})")

        # IST sanity: NSE session is 09:15-15:30 IST. Widen slightly to allow
        # for the fetch script's 09:00-15:45 request window. If times fall
        # far outside this (e.g. 03:xx-10:xx, the UTC equivalent), the data
        # is very likely NOT in IST as assumed.
        min_t, max_t = min(parsed).time(), max(parsed).time()
        session_lo, session_hi = datetime.strptime("08:45", "%H:%M").time(), \
                                  datetime.strptime("16:00", "%H:%M").time()
        if min_t < session_lo or max_t > session_hi:
            add("WARNING", "timestamps_outside_session",
                f"Candle range {min_t}-{max_t} falls outside expected "
                f"IST session window (08:45-16:00). Check timezone assumption.")

        # Monotonic non-decreasing (should be sorted by the fetch script already).
        if parsed != sorted(parsed):
            add("WARNING", "timestamps_not_sorted", "Candles are not in chronological order")

    # --------------------------------------------------------
    # 3. Candle count for the day
    # --------------------------------------------------------
    count = len(candles)
    if count < config.MIN_ACCEPTABLE_CANDLES:
        add("WARNING", "low_candle_count",
            f"{count} candles (expected ~{config.EXPECTED_CANDLES_PER_DAY}); "
            f"could be a half-day, holiday-adjacent session, or a gap")
    elif count > config.EXPECTED_CANDLES_PER_DAY + 15:
        add("WARNING", "high_candle_count",
            f"{count} candles, notably more than the expected "
            f"~{config.EXPECTED_CANDLES_PER_DAY}")

    # --------------------------------------------------------
    # 4. OHLC sanity + 5. Volume
    # --------------------------------------------------------
    bad_ohlc = 0
    zero_or_null_volume = 0
    for c in candles:
        try:
            o, h, l, cl = (float(c["open"]), float(c["high"]),
                           float(c["low"]), float(c["close"]))
        except (KeyError, TypeError, ValueError):
            bad_ohlc += 1
            continue

        if o <= 0 or h <= 0 or l <= 0 or cl <= 0:
            bad_ohlc += 1
            continue
        if h < max(o, cl, l) or l > min(o, cl, h) or h < l:
            bad_ohlc += 1
            continue

        vol = c.get("volume")
        if vol is None or float(vol) == 0:
            zero_or_null_volume += 1

    if bad_ohlc:
        add("CRITICAL", "invalid_ohlc", f"{bad_ohlc} candle(s) with non-sensical OHLC values")

    if candles:
        zero_ratio = zero_or_null_volume / len(candles)
        if zero_ratio > 0.10:
            add("WARNING", "zero_or_null_volume",
                f"{zero_or_null_volume}/{len(candles)} candles "
                f"({zero_ratio:.0%}) have zero/null volume")

    return count


# ============================================================
# MISSING TRADING DAYS
# ============================================================

def find_missing_trading_days(day_files, holidays, issues):
    if not day_files:
        return

    present = {d for d, _ in day_files}
    start, end = day_files[0][0], day_files[-1][0]

    cursor = start
    while cursor <= end:
        if not is_weekend(cursor) and cursor not in present:
            if cursor in holidays:
                issues.append({
                    "severity": "INFO",
                    "date": cursor.isoformat(),
                    "file": None,
                    "check": "known_holiday",
                    "detail": "No file present, matches supplied holiday calendar",
                })
            else:
                issues.append({
                    "severity": "WARNING",
                    "date": cursor.isoformat(),
                    "file": None,
                    "check": "missing_trading_day",
                    "detail": "Weekday with no raw file and not in the supplied "
                              "holiday list. Verify this wasn't a genuine "
                              "trading day (or supply --holidays).",
                })
        cursor += timedelta(days=1)


# ============================================================
# MAIN
# ============================================================

def run_validation(symbol, interval, holidays_path, report_path):
    day_files = discover_day_files(symbol, interval)

    issues = []

    if not day_files:
        print(f"No raw files found for {symbol}/{interval} under {config.RAW_ROOT}")
        return 1

    holidays = load_holidays(holidays_path)

    find_missing_trading_days(day_files, holidays, issues)

    total_candles = 0
    for day, file_path in day_files:
        total_candles += validate_day_file(day, file_path, issues)

    critical = [i for i in issues if i["severity"] == "CRITICAL"]
    warnings = [i for i in issues if i["severity"] == "WARNING"]
    infos = [i for i in issues if i["severity"] == "INFO"]

    report = {
        "symbol": symbol,
        "interval": interval,
        "generated_at": datetime.now().isoformat(),
        "days_found": len(day_files),
        "date_range": [day_files[0][0].isoformat(), day_files[-1][0].isoformat()],
        "total_candles": total_candles,
        "critical_count": len(critical),
        "warning_count": len(warnings),
        "info_count": len(infos),
        "issues": issues,
    }

    if report_path:
        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)

    # --------------------------------------------------------
    # Console summary
    # --------------------------------------------------------
    print("=" * 70)
    print(f"VALIDATION REPORT: {symbol} / {interval}")
    print("=" * 70)
    print(f"Days found      : {len(day_files)}  "
          f"({day_files[0][0]} -> {day_files[-1][0]})")
    print(f"Total candles   : {total_candles}")
    print(f"Critical issues : {len(critical)}")
    print(f"Warnings        : {len(warnings)}")
    print(f"Info            : {len(infos)}")
    print("-" * 70)

    for i in critical:
        print(f"[CRITICAL] {i['date']} | {i['check']}: {i['detail']}")
    for i in warnings:
        print(f"[WARNING]  {i['date']} | {i['check']}: {i['detail']}")

    print("=" * 70)
    if report_path:
        print(f"Full report written to {report_path}")

    return 1 if critical else 0


def main():
    parser = argparse.ArgumentParser(description="Validate raw Breeze JSON data.")
    parser.add_argument("--symbol", required=True, help="e.g. RELIND")
    parser.add_argument("--interval", default=config.INTERVAL_DIR)
    parser.add_argument("--holidays", default=None,
                         help="Optional JSON file with a list of YYYY-MM-DD holidays")
    parser.add_argument("--report", default=None,
                         help="Where to write the full JSON report "
                              "(default: data/validation_report_<symbol>.json)")
    args = parser.parse_args()

    report_path = args.report or os.path.join(
        config.DATA_ROOT, f"validation_report_{args.symbol}.json"
    )

    exit_code = run_validation(args.symbol, args.interval, args.holidays, report_path)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
