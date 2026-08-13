import os
import json
import time
import logging
import urllib.parse

from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from breeze_connect import BreezeConnect


# ============================================================
# LOAD ENVIRONMENT VARIABLES
# ============================================================

load_dotenv()


# ============================================================
# CONFIGURATION
# ============================================================

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
API_SESSION = os.getenv("API_SESSION")

STOCK_CODE = os.getenv("STOCK_CODE")

FROM_DATE = os.getenv("FROM_DATE")
TO_DATE = os.getenv("TO_DATE")

EXCHANGE_CODE = os.getenv(
    "EXCHANGE_CODE",
    "NSE"
)

PRODUCT_TYPE = os.getenv(
    "PRODUCT_TYPE",
    "cash"
)

INTERVAL = os.getenv(
    "INTERVAL",
    "1minute"
)

REQUEST_DELAY = float(
    os.getenv(
        "REQUEST_DELAY",
        "1"
    )
)

MAX_RETRIES = int(
    os.getenv(
        "MAX_RETRIES",
        "3"
    )
)

DATA_ROOT = os.getenv(
    "DATA_ROOT",
    "data/raw/breeze"
)


# ============================================================
# CONSTANTS
# ============================================================

# Maximum number of trading days requested in one API call.
#
# 2 trading days ~= 750 one-minute candles
#
# This stays safely below the 1,000 candle limit.
#
CHUNK_TRADING_DAYS = 2


# ------------------------------------------------------------
# Market session
# ------------------------------------------------------------

MARKET_START_HOUR = 9
MARKET_START_MINUTE = 0

MARKET_END_HOUR = 15
MARKET_END_MINUTE = 45


# ============================================================
# VALIDATE ENVIRONMENT
# ============================================================

required_variables = {
    "API_KEY": API_KEY,
    "API_SECRET": API_SECRET,
    "API_SESSION": API_SESSION,
    "STOCK_CODE": STOCK_CODE,
    "FROM_DATE": FROM_DATE,
    "TO_DATE": TO_DATE,
}


for name, value in required_variables.items():

    if not value:

        raise ValueError(
            f"{name} is missing from .env"
        )


# ============================================================
# DATE PARSING
# ============================================================

def parse_api_datetime(date_string):
    """
    Parse datetime used in API configuration.

    Expected format:

        2026-01-01T09:00:00.000Z
    """

    date_string = str(
        date_string
    ).strip()


    formats = [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
    ]


    for date_format in formats:

        try:

            return datetime.strptime(
                date_string,
                date_format
            ).replace(
                tzinfo=timezone.utc
            )

        except ValueError:

            continue


    raise ValueError(
        f"Invalid API datetime format: "
        f"{date_string}"
    )


# ============================================================
# IMPORTANT:
#
# Breeze returns candle timestamps differently.
#
# Example:
#
#     2026-01-09 12:39:00
#
# NOT:
#
#     2026-01-09T12:39:00.000Z
#
# ============================================================

def parse_candle_datetime(date_string):
    """
    Parse datetime returned by Breeze.

    Breeze normally returns:

        2026-01-09 12:39:00

    But this function also accepts ISO formats
    just in case the API changes.
    """

    if not date_string:

        raise ValueError(
            "Candle datetime is empty"
        )


    date_string = str(
        date_string
    ).strip()


    # --------------------------------------------------------
    # Breeze normal response format
    # --------------------------------------------------------

    try:

        return datetime.strptime(
            date_string,
            "%Y-%m-%d %H:%M:%S"
        )

    except ValueError:

        pass


    # --------------------------------------------------------
    # ISO format with milliseconds
    # --------------------------------------------------------

    try:

        return datetime.strptime(
            date_string,
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )

    except ValueError:

        pass


    # --------------------------------------------------------
    # ISO format without milliseconds
    # --------------------------------------------------------

    try:

        return datetime.strptime(
            date_string,
            "%Y-%m-%dT%H:%M:%SZ"
        )

    except ValueError:

        pass


    raise ValueError(
        f"Unsupported candle datetime format: "
        f"{date_string}"
    )


# ============================================================
# FORMAT DATE FOR BREEZE API
# ============================================================

def format_date(dt):
    """
    Format datetime for Breeze API.

    Output:

        2026-01-01T09:00:00.000Z

    IMPORTANT:
    Python gives 6 microseconds:

        .000000

    We remove the last 3 digits:

        .000
    """

    return (
        dt.astimezone(
            timezone.utc
        )
        .strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3]
        + "Z"
    )


# ============================================================
# MARKET DATE HELPERS
# ============================================================

def date_to_market_start(date_value):
    """
    Return 09:00 UTC for the given date.
    """

    return datetime(
        year=date_value.year,
        month=date_value.month,
        day=date_value.day,
        hour=MARKET_START_HOUR,
        minute=MARKET_START_MINUTE,
        second=0,
        microsecond=0,
        tzinfo=timezone.utc
    )


def date_to_market_end(date_value):
    """
    Return 15:45 UTC for the given date.
    """

    return datetime(
        year=date_value.year,
        month=date_value.month,
        day=date_value.day,
        hour=MARKET_END_HOUR,
        minute=MARKET_END_MINUTE,
        second=0,
        microsecond=0,
        tzinfo=timezone.utc
    )


# ============================================================
# WEEKEND HELPERS
# ============================================================

def is_weekend(date_value):
    """
    Monday    = 0
    Tuesday   = 1
    Wednesday = 2
    Thursday  = 3
    Friday    = 4
    Saturday  = 5
    Sunday    = 6
    """

    return date_value.weekday() >= 5


def next_trading_day(date_value):
    """
    Get next Monday-Friday date.

    Friday -> Monday
    Saturday -> Monday
    Sunday -> Monday
    """

    next_date = (
        date_value
        + timedelta(days=1)
    )


    while is_weekend(
        next_date
    ):

        next_date += timedelta(
            days=1
        )


    return next_date


def add_trading_days(
    start_date,
    number_of_days
):
    """
    Add trading days while skipping weekends.
    """

    current_date = start_date

    days_added = 0


    while days_added < number_of_days:

        current_date += timedelta(
            days=1
        )


        if not is_weekend(
            current_date
        ):

            days_added += 1


    return current_date


# ============================================================
# PARSE FROM / TO
# ============================================================

start_datetime = parse_api_datetime(
    FROM_DATE
)

end_datetime = parse_api_datetime(
    TO_DATE
)


if start_datetime >= end_datetime:

    raise ValueError(
        "FROM_DATE must be before TO_DATE"
    )


# ============================================================
# DATA DIRECTORY
# ============================================================

# Result:
#
# data/raw/breeze/RELIND/1m/
#
STOCK_DATA_DIR = os.path.join(
    DATA_ROOT,
    STOCK_CODE,
    "1m"
)


os.makedirs(
    STOCK_DATA_DIR,
    exist_ok=True
)


# ============================================================
# LOGGING
# ============================================================

os.makedirs(
    "logs",
    exist_ok=True
)


run_timestamp = datetime.now().strftime(
    "%Y%m%d_%H%M%S"
)


log_file = os.path.join(
    "logs",
    f"{STOCK_CODE}_{INTERVAL}_{run_timestamp}.log"
)


logger = logging.getLogger(
    "breeze_historical_data"
)


logger.setLevel(
    logging.INFO
)


# Prevent duplicate handlers if script
# is imported/reloaded.
logger.handlers.clear()


formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


file_handler = logging.FileHandler(
    log_file,
    encoding="utf-8"
)

file_handler.setFormatter(
    formatter
)


console_handler = logging.StreamHandler()

console_handler.setFormatter(
    formatter
)


logger.addHandler(
    file_handler
)

logger.addHandler(
    console_handler
)


# ============================================================
# START LOG
# ============================================================

logger.info("=" * 70)
logger.info("BREEZE HISTORICAL DATA DOWNLOADER")
logger.info("=" * 70)

logger.info(
    f"Stock          : {STOCK_CODE}"
)

logger.info(
    f"Exchange       : {EXCHANGE_CODE}"
)

logger.info(
    f"Product        : {PRODUCT_TYPE}"
)

logger.info(
    f"Interval       : {INTERVAL}"
)

logger.info(
    f"From           : {FROM_DATE}"
)

logger.info(
    f"To             : {TO_DATE}"
)

logger.info(
    "Market session : "
    f"{MARKET_START_HOUR:02d}:"
    f"{MARKET_START_MINUTE:02d}"
    " -> "
    f"{MARKET_END_HOUR:02d}:"
    f"{MARKET_END_MINUTE:02d}"
)

logger.info(
    f"Chunk days     : {CHUNK_TRADING_DAYS}"
)

logger.info(
    f"Request delay  : {REQUEST_DELAY}s"
)

logger.info(
    f"Max retries    : {MAX_RETRIES}"
)

logger.info(
    f"Data directory : {STOCK_DATA_DIR}"
)

logger.info(
    f"Log file       : {log_file}"
)

logger.info("=" * 70)


# ============================================================
# INITIALIZE BREEZE
# ============================================================

logger.info(
    "Initializing BreezeConnect..."
)


breeze = BreezeConnect(
    api_key=API_KEY
)


# ============================================================
# LOGIN URL
# ============================================================

login_url = (
    "https://api.icicidirect.com/apiuser/login?api_key="
    + urllib.parse.quote_plus(
        API_KEY
    )
)


logger.info(
    "Breeze login URL generated."
)


# ============================================================
# GENERATE SESSION
# ============================================================

logger.info(
    "Generating Breeze session..."
)


breeze.generate_session(
    api_secret=API_SECRET,
    session_token=API_SESSION
)


logger.info(
    "Breeze session generated successfully."
)


# ============================================================
# DAILY FILE PATH
# ============================================================

def get_daily_file_path(
    date_value
):
    """
    Generate:

    data/raw/breeze/
        RELIND/
            1m/
                2026/
                    01/
                        2026-01-01.json
    """

    year = str(
        date_value.year
    )

    month = f"{date_value.month:02d}"

    filename = (
        f"{date_value.isoformat()}.json"
    )


    directory = os.path.join(
        STOCK_DATA_DIR,
        year,
        month
    )


    os.makedirs(
        directory,
        exist_ok=True
    )


    return os.path.join(
        directory,
        filename
    )


# ============================================================
# CHECK IF DAY IS ALREADY DOWNLOADED
# ============================================================

def is_day_downloaded(
    date_value
):
    """
    A day is considered downloaded ONLY when:

        status == "data"

    An empty file is NOT considered complete.

    This is important because an earlier version of
    the script could have created empty files due to
    the datetime parsing bug.
    """

    file_path = get_daily_file_path(
        date_value
    )


    if not os.path.exists(
        file_path
    ):

        return False


    try:

        with open(
            file_path,
            "r",
            encoding="utf-8"
        ) as json_file:

            data = json.load(
                json_file
            )


        if not isinstance(
            data,
            dict
        ):

            return False


        # ----------------------------------------------------
        # Only "data" means completed.
        #
        # "empty" means download again.
        # ----------------------------------------------------

        if data.get(
            "status"
        ) != "data":

            return False


        if not isinstance(
            data.get("data"),
            list
        ):

            return False


        if len(
            data["data"]
        ) == 0:

            return False


        return True


    except Exception as error:

        logger.warning(
            f"Could not validate existing file "
            f"{file_path}: {error}"
        )

        return False


# ============================================================
# SAVE DAILY DATA
# ============================================================

def save_daily_data(
    date_value,
    candles,
    request_from,
    request_to
):
    """
    Save candles belonging to one trading day.

    Output example:

    {
        "stock_code": "RELIND",
        "date": "2026-01-09",
        "total_candles": 375,
        "status": "data",
        "data": [...]
    }
    """

    file_path = get_daily_file_path(
        date_value
    )


    # --------------------------------------------------------
    # Remove duplicate timestamps
    # --------------------------------------------------------

    unique_candles = {}


    for candle in candles:

        timestamp = candle.get(
            "datetime"
        )


        if timestamp:

            unique_candles[
                timestamp
            ] = candle


    candles = list(
        unique_candles.values()
    )


    # --------------------------------------------------------
    # Sort chronologically
    # --------------------------------------------------------

    candles.sort(
        key=lambda x: x.get(
            "datetime",
            ""
        )
    )


    # --------------------------------------------------------
    # Create file data
    # --------------------------------------------------------

    daily_data = {

        "stock_code": STOCK_CODE,

        "exchange_code": EXCHANGE_CODE,

        "product_type": PRODUCT_TYPE,

        "interval": INTERVAL,

        "date": date_value.isoformat(),

        "market_start": (
            f"{date_value.isoformat()}"
            "T09:00:00.000Z"
        ),

        "market_end": (
            f"{date_value.isoformat()}"
            "T15:45:00.000Z"
        ),

        "request_from": format_date(
            request_from
        ),

        "request_to": format_date(
            request_to
        ),

        "total_candles": len(
            candles
        ),

        "status": (
            "data"
            if len(candles) > 0
            else "empty"
        ),

        "data": candles
    }


    # --------------------------------------------------------
    # Temporary file
    #
    # Prevents a partially-written JSON file from looking
    # like a completed download.
    # --------------------------------------------------------

    temp_file = (
        file_path
        + ".tmp"
    )


    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as json_file:

        json.dump(
            daily_data,
            json_file,
            indent=4,
            ensure_ascii=False
        )


    # Replace final file atomically
    os.replace(
        temp_file,
        file_path
    )


    logger.info(
        f"Saved {date_value}: "
        f"{len(candles)} candles -> "
        f"{file_path}"
    )


# ============================================================
# SPLIT CANDLES BY DATE
# ============================================================

def split_candles_by_date(
    candles
):
    """
    Breeze response timestamps look like:

        2026-01-09 12:39:00

    Split them into:

        {
            2026-01-09: [...],
            2026-01-12: [...]
        }
    """

    candles_by_date = {}


    for candle in candles:

        timestamp = candle.get(
            "datetime"
        )


        if not timestamp:

            logger.warning(
                "Candle does not contain "
                "'datetime'. Skipping."
            )

            continue


        try:

            candle_datetime = (
                parse_candle_datetime(
                    timestamp
                )
            )


            candle_date = (
                candle_datetime.date()
            )


        except Exception as error:

            logger.warning(
                f"Could not parse candle "
                f"datetime '{timestamp}': "
                f"{error}"
            )

            continue


        if candle_date not in candles_by_date:

            candles_by_date[
                candle_date
            ] = []


        candles_by_date[
            candle_date
        ].append(
            candle
        )


    return candles_by_date


# ============================================================
# FETCH ONE API CHUNK
# ============================================================

def fetch_chunk(
    chunk_from,
    chunk_to
):
    """
    Make one Breeze API request.

    Maximum requested range:

        2 trading days

    """

    from_date = format_date(
        chunk_from
    )

    to_date = format_date(
        chunk_to
    )


    for attempt in range(
        1,
        MAX_RETRIES + 1
    ):

        try:

            logger.info(
                f"API request: "
                f"{from_date} -> "
                f"{to_date} "
                f"(attempt "
                f"{attempt}/{MAX_RETRIES})"
            )


            data = (
                breeze.get_historical_data_v2(

                    interval=INTERVAL,

                    from_date=from_date,

                    to_date=to_date,

                    stock_code=STOCK_CODE,

                    exchange_code=EXCHANGE_CODE,

                    product_type=PRODUCT_TYPE
                )
            )


            # =================================================
            # DEBUG / RESPONSE SUMMARY
            # =================================================

            if data is None:

                logger.warning(
                    "Breeze returned None."
                )

                return []


            if not isinstance(
                data,
                dict
            ):

                logger.warning(
                    "Unexpected Breeze response type: "
                    f"{type(data)}"
                )

                return []


            # =================================================
            # CHECK ERROR
            # =================================================

            if data.get(
                "Success"
            ) is None:

                error_message = data.get(
                    "Error",
                    "Unknown API error"
                )

                status = data.get(
                    "Status",
                    "Unknown"
                )


                logger.warning(
                    f"Breeze API error: "
                    f"{error_message} "
                    f"(status={status})"
                )


                # ------------------------------------------------
                # Date format errors are not retryable.
                # ------------------------------------------------

                if (
                    "Date not in Proper Format"
                    in str(error_message)
                ):

                    raise ValueError(
                        "Breeze rejected date format: "
                        f"{from_date} -> {to_date}"
                    )


                # ------------------------------------------------
                # Retry other errors
                # ------------------------------------------------

                if attempt < MAX_RETRIES:

                    wait_time = (
                        attempt * 2
                    )

                    logger.info(
                        f"Retrying in "
                        f"{wait_time} seconds..."
                    )

                    time.sleep(
                        wait_time
                    )

                    continue


                raise RuntimeError(
                    f"Breeze API error: "
                    f"{error_message}"
                )


            # =================================================
            # SUCCESS
            # =================================================

            success_data = data.get(
                "Success"
            )


            if not isinstance(
                success_data,
                list
            ):

                logger.warning(
                    "Breeze Success field is "
                    "not a list."
                )

                return []


            logger.info(
                f"Breeze returned "
                f"{len(success_data)} candles."
            )


            # ------------------------------------------------
            # Log first/last timestamp for debugging.
            # ------------------------------------------------

            if success_data:

                first_timestamp = (
                    success_data[0].get(
                        "datetime"
                    )
                )

                last_timestamp = (
                    success_data[-1].get(
                        "datetime"
                    )
                )


                logger.info(
                    f"First candle : "
                    f"{first_timestamp}"
                )

                logger.info(
                    f"Last candle  : "
                    f"{last_timestamp}"
                )


            return success_data


        except ValueError:

            # Date formatting error.
            # Don't retry.
            raise


        except Exception as error:

            logger.exception(
                f"API request failed: "
                f"{error}"
            )


            if attempt < MAX_RETRIES:

                wait_time = (
                    attempt * 2
                )


                logger.info(
                    f"Retrying in "
                    f"{wait_time} seconds..."
                )


                time.sleep(
                    wait_time
                )


            else:

                logger.error(
                    "Maximum retries reached."
                )

                raise


    return []


# ============================================================
# FIND NEXT MISSING CHUNK
# ============================================================

def get_next_missing_chunk(
    start_date,
    final_date
):
    """
    Find the next missing trading-day chunk.

    Rules:

    Monday + Tuesday
    Wednesday + Thursday
    Friday only
    Monday + Tuesday
    ...

    Never:

    Friday + Monday

    Never:

    Saturday/Sunday
    """

    current_date = start_date


    # --------------------------------------------------------
    # Find first missing weekday
    # --------------------------------------------------------

    while current_date <= final_date:

        # Skip Saturday/Sunday
        if is_weekend(
            current_date
        ):

            current_date = (
                next_trading_day(
                    current_date
                )
            )

            continue


        # Existing valid file
        if is_day_downloaded(
            current_date
        ):

            logger.info(
                f"{current_date} already "
                f"downloaded. Skipping."
            )


            current_date = (
                next_trading_day(
                    current_date
                )
            )

            continue


        # Found missing day
        break


    # --------------------------------------------------------
    # Everything is complete
    # --------------------------------------------------------

    if current_date > final_date:

        return None


    # --------------------------------------------------------
    # Friday must ALWAYS be a single-day request.
    #
    # This prevents:
    #
    # Friday -> Monday
    # --------------------------------------------------------

    if current_date.weekday() == 4:

        return (
            current_date,
            current_date
        )


    # --------------------------------------------------------
    # Try to include the next trading day.
    # --------------------------------------------------------

    second_date = add_trading_days(
        current_date,
        1
    )


    # --------------------------------------------------------
    # Can't include next date if:
    #
    # 1. Outside requested range
    # 2. Already downloaded
    # --------------------------------------------------------

    if (
        second_date > final_date
        or is_day_downloaded(
            second_date
        )
    ):

        return (
            current_date,
            current_date
        )


    return (
        current_date,
        second_date
    )


# ============================================================
# MAIN DOWNLOAD LOOP
# ============================================================

logger.info("")
logger.info("=" * 70)
logger.info("STARTING DOWNLOAD")
logger.info("=" * 70)


current_date = start_datetime.date()

final_date = end_datetime.date()

chunk_number = 0


while current_date <= final_date:

    # ========================================================
    # FIND NEXT MISSING CHUNK
    # ========================================================

    chunk = get_next_missing_chunk(
        current_date,
        final_date
    )


    if chunk is None:

        logger.info(
            "All requested trading days "
            "are already downloaded."
        )

        break


    chunk_start_date, chunk_end_date = chunk


    chunk_number += 1


    # ========================================================
    # REQUEST TIMES
    #
    # Always:
    #
    # 09:00 -> 15:45
    # ========================================================

    chunk_from = date_to_market_start(
        chunk_start_date
    )

    chunk_to = date_to_market_end(
        chunk_end_date
    )


    # ========================================================
    # RESPECT FROM_DATE
    #
    # Only affects first requested day.
    # ========================================================

    if (
        chunk_start_date
        == start_datetime.date()
    ):

        if start_datetime > chunk_from:

            chunk_from = start_datetime


    # ========================================================
    # RESPECT TO_DATE
    #
    # Only affects final requested day.
    # ========================================================

    if (
        chunk_end_date
        == end_datetime.date()
    ):

        if end_datetime < chunk_to:

            chunk_to = end_datetime


    # ========================================================
    # CHUNK LOG
    # ========================================================

    logger.info("")
    logger.info("=" * 70)

    logger.info(
        f"CHUNK #{chunk_number}"
    )

    logger.info(
        f"Trading days: "
        f"{chunk_start_date} -> "
        f"{chunk_end_date}"
    )

    logger.info(
        f"Request: "
        f"{format_date(chunk_from)} -> "
        f"{format_date(chunk_to)}"
    )

    logger.info("=" * 70)


    # ========================================================
    # FETCH
    # ========================================================

    candles = fetch_chunk(
        chunk_from,
        chunk_to
    )


    # ========================================================
    # SPLIT RESPONSE BY DATE
    # ========================================================

    candles_by_date = (
        split_candles_by_date(
            candles
        )
    )


    # ========================================================
    # SAVE EACH TRADING DAY
    # ========================================================

    date_to_save = chunk_start_date


    while date_to_save <= chunk_end_date:

        # Safety check
        if is_weekend(
            date_to_save
        ):

            date_to_save += timedelta(
                days=1
            )

            continue


        day_candles = (
            candles_by_date.get(
                date_to_save,
                []
            )
        )


        save_daily_data(

            date_value=date_to_save,

            candles=day_candles,

            request_from=chunk_from,

            request_to=chunk_to
        )


        date_to_save += timedelta(
            days=1
        )


    # ========================================================
    # MOVE TO NEXT TRADING DAY
    #
    # IMPORTANT:
    #
    # No +1 minute.
    #
    # We move by DATE.
    # ========================================================

    current_date = (
        next_trading_day(
            chunk_end_date
        )
    )


    # ========================================================
    # REQUEST DELAY
    # ========================================================

    if current_date <= final_date:

        logger.info(
            f"Next trading day: "
            f"{current_date}"
        )

        logger.info(
            f"Waiting "
            f"{REQUEST_DELAY} seconds..."
        )


        time.sleep(
            REQUEST_DELAY
        )


# ============================================================
# FINAL SUMMARY
# ============================================================

logger.info("")
logger.info("=" * 70)
logger.info("DOWNLOAD COMPLETE")
logger.info("=" * 70)


# Count files
downloaded_files = []


for root, dirs, files in os.walk(
    STOCK_DATA_DIR
):

    for filename in files:

        if (
            filename.endswith(".json")
            and not filename.endswith(".tmp")
        ):

            downloaded_files.append(
                os.path.join(
                    root,
                    filename
                )
            )


logger.info(
    f"Daily files present: "
    f"{len(downloaded_files)}"
)

logger.info(
    f"Data directory: "
    f"{STOCK_DATA_DIR}"
)

logger.info(
    f"Log file: "
    f"{log_file}"
)

logger.info("=" * 70)