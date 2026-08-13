# data_fetch_script.py — Full Context

## 1. Purpose

`data_fetch_script.py` downloads historical 1-minute NSE equity data from ICICI Direct Breeze using the `breeze_connect` Python SDK.

The script must:

- Read credentials from `.env`
- Read stock/date/output configuration from `.env`
- Authenticate using the known-working Breeze authentication flow
- Download historical 1-minute data
- Use maximum 2 trading days per API request
- Respect the API's ~1,000 candle response limit
- Avoid Saturday and Sunday
- Never request Friday + Monday together
- Keep market request times at 09:00 -> 15:45
- Save data incrementally
- Save one JSON file per trading day
- Resume from already downloaded days
- Retry transient API errors
- Respect a delay between API requests
- Create dynamic logs
- Correctly parse Breeze response timestamps

---

# 2. Known-working authentication

The following code has been tested successfully.

This should be considered the baseline authentication implementation.

```python
import os
import urllib.parse
from dotenv import load_dotenv
from breeze_connect import BreezeConnect

load_dotenv()

api_key = os.getenv("API_KEY")
api_secret = os.getenv("API_SECRET")
apisession = os.getenv("API_SESSION")

if not api_key:
    raise ValueError("API_KEY is missing from .env")

if not api_secret:
    raise ValueError("API_SECRET is missing from .env")

if not apisession:
    raise ValueError("API_SESSION is missing from .env")

breeze = BreezeConnect(
    api_key=api_key
)

login_url = (
    "https://api.icicidirect.com/apiuser/login?api_key="
    + urllib.parse.quote_plus(api_key)
)

print("Login URL:")
print(login_url)

breeze.generate_session(
    api_secret=api_secret,
    session_token=apisession
)



This exact authentication flow is known to work.

Do not unnecessarily modify it when changing the downloader.

3. Environment variables

The .env file should contain:

API_KEY=your_api_key
API_SECRET=your_api_secret
API_SESSION=your_api_session

STOCK_CODE=RELIND

FROM_DATE=2026-01-01T09:00:00.000Z
TO_DATE=2026-07-31T15:45:00.000Z

EXCHANGE_CODE=NSE
PRODUCT_TYPE=cash
INTERVAL=1minute

REQUEST_DELAY=1
MAX_RETRIES=3

DATA_ROOT=data/raw/breeze

Required secrets:

API_KEY
API_SECRET
API_SESSION

Do not put these secrets directly inside the Python script.

Do not print them in logs.

4. Dependencies

Required packages:

breeze-connect
python-dotenv

Install with:

py -m pip install breeze-connect python-dotenv

Run the downloader with:

py .\data_fetch_script.py
5. Known-working historical API request

The user's original working request is:

data = breeze.get_historical_data_v2(
    interval="1minute",
    from_date="2026-01-01T09:00:00.000Z",
    to_date="2026-01-01T09:45:30.000Z",
    stock_code="RELIND",
    exchange_code="NSE",
    product_type="cash"
)

This request successfully returns data.

If the full downloader stops working, test this simple request first.

6. Important API date format

Breeze expects request dates in this format:

2026-01-01T09:00:00.000Z

It does NOT accept:

2026-01-01T09:00:00.0000Z

The latter caused:

Date not in Proper Format

The formatter should therefore produce exactly 3 digits for milliseconds.

Correct:

def format_date(dt):
    return (
        dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        + "Z"
    )

Example:

2026-07-01T09:00:00.000Z
7. Important API response timestamp format

The request timestamp and response timestamp are different.

Breeze can return candles like:

2026-01-09 12:39:00

The downloader must parse this using:

datetime.strptime(
    timestamp,
    "%Y-%m-%d %H:%M:%S"
)

A previous version incorrectly expected:

2026-01-09T12:39:00.000Z

This caused:

Could not parse candle datetime

and resulted in valid API data being discarded.

This is one of the most important fixes in the downloader.

8. Market session

The downloader should use:

09:00 -> 15:45

for each request.

The request should not be moved forward by one minute after each response.

Do NOT do:

last candle + 1 minute

Instead, the downloader should move forward by trading date.

For example:

Monday 09:00 -> Tuesday 15:45

then:

Wednesday 09:00 -> Thursday 15:45

then:

Friday 09:00 -> Friday 15:45
9. API candle limit

For 1-minute data:

Maximum candles per request ≈ 1,000

Indian equity market session:

375 minutes/day

Therefore:

2 trading days ≈ 750 candles

Two trading days is used as the safe chunk size.

The downloader should therefore never intentionally request more than:

2 trading days

in one 1-minute request.

10. Chunking rules

The desired chunk pattern is:

Monday + Tuesday
Wednesday + Thursday
Friday
Monday + Tuesday
Wednesday + Thursday
Friday
...

Never:

Friday + Saturday

Never:

Friday + Monday

Never:

Saturday + Sunday

Saturday and Sunday should be skipped before making an API request.

11. Weekend handling

Python weekday values:

Monday    = 0
Tuesday   = 1
Wednesday = 2
Thursday  = 3
Friday    = 4
Saturday  = 5
Sunday    = 6

The downloader should use something equivalent to:

def is_weekend(date_value):
    return date_value.weekday() >= 5

Saturday and Sunday must never be requested.

If the current date is Friday:

Friday -> Friday

Then:

Saturday -> skip
Sunday -> skip
Monday -> continue

Next request:

Monday + Tuesday
12. Date range

The date range comes from:

FROM_DATE=2026-01-01T09:00:00.000Z
TO_DATE=2026-07-31T15:45:00.000Z

The downloader should continue until the entire range has been processed.

It should:

Start from FROM_DATE
Skip weekends
Create 2-trading-day chunks
Make Friday a single-day chunk
Respect the start time on the first day
Respect the end time on the final day
Continue until TO_DATE
13. Output structure

Do NOT create one huge JSON file for years of data.

Instead:

data/
└── raw/
    └── breeze/
        └── RELIND/
            └── 1m/
                └── 2026/
                    ├── 01/
                    │   ├── 2026-01-01.json
                    │   ├── 2026-01-02.json
                    │   ├── 2026-01-05.json
                    │   └── ...
                    ├── 02/
                    ├── 03/
                    ├── 04/
                    ├── 05/
                    ├── 06/
                    └── 07/

The path is dynamic based on:

STOCK_CODE
INTERVAL
YEAR
MONTH
DATE

Example:

data/raw/breeze/RELIND/1m/2026/01/2026-01-09.json
14. Daily JSON format

Each daily file should contain metadata plus the actual Breeze response candles.

Example:

{
    "stock_code": "RELIND",
    "exchange_code": "NSE",
    "product_type": "cash",
    "interval": "1minute",
    "date": "2026-01-09",
    "market_start": "2026-01-09T09:00:00.000Z",
    "market_end": "2026-01-09T15:45:00.000Z",
    "request_from": "2026-01-08T09:00:00.000Z",
    "request_to": "2026-01-09T15:45:00.000Z",
    "total_candles": 375,
    "status": "data",
    "data": [
        {
            "datetime": "2026-01-09 09:15:00"
        }
    ]
}

The actual candle fields should be preserved exactly as returned by Breeze.

15. Empty files

An earlier version generated files like:

{
    "status": "empty",
    "data": []
}

because the candle timestamp parser was wrong.

An empty file must NOT be treated as a completed download.

Only a file with:

"status": "data"

and a non-empty:

"data": [...]

should be considered successfully downloaded.

16. Restartability

The downloader must be restartable.

For example:

January     downloaded
February    downloaded
March       downloaded
April       downloaded
May         downloaded
June        downloaded
July        partially downloaded

If the process crashes, running it again should skip valid existing files and continue from missing/incomplete days.

Example:

2026-01-01.json -> valid -> skip
2026-01-02.json -> valid -> skip
2026-01-05.json -> valid -> skip
...
2026-07-30.json -> valid -> skip
2026-07-31.json -> missing -> download

If an existing file contains:

"status": "empty"

it must be downloaded again.

If JSON is malformed, it must be downloaded again.

17. Split two-day API responses

A two-day API request may return:

2026-01-08 09:15:00
...
2026-01-08 15:29:00

2026-01-09 09:15:00
...
2026-01-09 15:29:00

The downloader must split this response into:

2026-01-08.json
2026-01-09.json

Each file should contain only candles belonging to that date.

18. Deduplication

Before saving a day's data:

Use datetime as the candle key.
Remove duplicate timestamps.
Sort chronologically.
Save the result.

Example:

unique_candles = {}

for candle in candles:
    timestamp = candle.get("datetime")

    if timestamp:
        unique_candles[timestamp] = candle

candles = list(unique_candles.values())

candles.sort(
    key=lambda x: x.get("datetime", "")
)
19. Atomic file writes

A daily file should preferably be written as:

2026-01-09.json.tmp

and then renamed to:

2026-01-09.json

using:

os.replace(
    temp_file,
    file_path
)

This prevents a crash during writing from leaving a corrupt JSON file that looks complete.

20. Logging

Each run should generate a dynamic log file.

Example:

logs/
└── RELIND_1minute_20260812_233133.log

Log information should include:

Stock
Exchange
Product
Interval
From
To
Market session
Chunk size
Request delay
Max retries
Data directory
Log file
Session initialization
Chunk number
Request range
API response count
First candle
Last candle
Saved file
Skipped file
Retry
Errors
Final summary

Do NOT log:

API_SECRET
API_SESSION

or any other sensitive credentials.

21. Retry logic

Configuration:

MAX_RETRIES=3

For transient failures:

Attempt 1
    ↓
wait 2 seconds
    ↓
Attempt 2
    ↓
wait 4 seconds
    ↓
Attempt 3
    ↓
fail

However, deterministic errors should not simply be retried.

For example:

Date not in Proper Format

means the request construction is wrong.

Fix the date format instead of repeatedly sending the same invalid request.

22. Request delay

Configuration:

REQUEST_DELAY=1

After each chunk request, the downloader should wait:

1 second

before making the next request.

This is intended to provide safe pacing.

The delay does not replace any official Breeze rate-limit requirements if Breeze imposes stricter limits.

23. Important previous bugs
Bug 1 — Wrong request timestamp

Wrong:

2026-07-01T09:15:00.0000Z

Correct:

2026-07-01T09:15:00.000Z

The extra 0 caused:

Date not in Proper Format
Bug 2 — Wrong response timestamp parser

Actual Breeze response:

2026-01-09 12:39:00

Incorrect parser:

"%Y-%m-%dT%H:%M:%S.%fZ"

Correct parser:

"%Y-%m-%d %H:%M:%S"
Bug 3 — Empty files were treated as successful

A file containing:

"status": "empty"

must be considered incomplete.

The downloader should retry it on the next run.

Bug 4 — Weekend data

The exchange/API may contain activity on Saturday/Sunday.

The downloader intentionally avoids these dates.

Bug 5 — Friday to Monday request

Do not make:

Friday 09:00 -> Monday 15:45

Instead:

Friday 09:00 -> Friday 15:45

then skip:

Saturday
Sunday

then:

Monday 09:00 -> Tuesday 15:45
24. Project structure

Recommended:

backtest/
└── breeze/
    ├── .env
    ├── .gitignore
    ├── data_fetch_script.py
    ├── data_fetch_script.md
    ├── logs/
    └── data/
        └── raw/
            └── breeze/
                └── RELIND/
                    └── 1m/
                        └── 2026/
                            ├── 01/
                            ├── 02/
                            ├── 03/
                            └── ...
25. Running

From PowerShell:

cd C:\yadvendra\trading\backtest\breeze

Then:

py .\data_fetch_script.py
26. Troubleshooting authentication

The user's small test script successfully performs:

breeze = BreezeConnect(
    api_key=api_key
)

breeze.generate_session(
    api_secret=api_secret,
    session_token=apisession
)

Therefore this is the trusted authentication baseline.

If the large downloader appears stuck at:

Generating Breeze session...

first test the minimal authentication script.

Do not immediately assume the historical-data chunking is responsible.

27. Troubleshooting historical data

If authentication works but no data is saved:

Step 1

Test a single known-working request:

data = breeze.get_historical_data_v2(
    interval="1minute",
    from_date="2026-01-01T09:00:00.000Z",
    to_date="2026-01-01T09:45:30.000Z",
    stock_code="RELIND",
    exchange_code="NSE",
    product_type="cash"
)
Step 2

Check the raw response.

Step 3

Check the first candle:

print(data["Success"][0])
Step 4

Confirm its timestamp looks like:

2026-01-01 09:15:00
Step 5

Only then debug chunking and file saving.

28. Important design rules

Future modifications should preserve these rules:

Keep the known-working Breeze authentication code.
Never expose secrets.
Use .000Z request timestamps.
Parse Breeze candle timestamps using YYYY-MM-DD HH:MM:SS.
Use trading-date pagination.
Do not use last_timestamp + 1 minute.
Use maximum two trading days per 1-minute API request.
Never request Saturday or Sunday.
Never combine Friday with Monday.
Save daily files.
Treat empty files as incomplete.
Make file writes atomic.
Support restart/resume.
Respect request delay.
Retry transient failures.
Do not manufacture candles.
Preserve the raw Breeze candle fields.
Keep detailed logs.
29. Overall flow
Load .env
    ↓
Validate API credentials
    ↓
Initialize BreezeConnect
    ↓
Generate Breeze session
    ↓
Read FROM_DATE and TO_DATE
    ↓
Find next missing trading day
    ↓
Skip Saturday/Sunday
    ↓
Create 2-trading-day chunk
    ↓
If Friday → use Friday only
    ↓
Build request:
09:00 -> 15:45
    ↓
Call Breeze
    ↓
Receive candle data
    ↓
Parse:
YYYY-MM-DD HH:MM:SS
    ↓
Split candles by date
    ↓
Deduplicate
    ↓
Sort
    ↓
Write daily JSON
    ↓
Wait REQUEST_DELAY
    ↓
Move to next trading day
    ↓
Repeat
    ↓
Finish when FROM_DATE -> TO_DATE is complete
30. Security

Never put real credentials in this file.

Never put real credentials in:

data_fetch_script.md
README.md
GitHub
logs
source code

Keep credentials in:

.env

and make sure .env is ignored by Git.

If credentials have accidentally been exposed publicly, rotate them through the appropriate ICICI Direct/Breeze system.