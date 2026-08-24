# `./data` structure

Reference only — do not open files under `./data` unless the task explicitly
requires it. This file exists so Claude doesn't have to `ls`/read into
`./data` (large, token-expensive) just to know the layout. Canonical schema
and path constants are the source of truth in `data_pipeline/config.py`
(`RAW_ROOT`, `PROCESSED_ROOT`, `FEATURES_ROOT`, `CANONICAL_COLUMNS`) —
update this file if that layout changes.

```
data/
├── raw/
│   └── breeze/
│       └── <SYMBOL>/                    e.g. RELIND, NIFTY
│           └── 1m/
│               └── <YYYY>/
│                   └── <MM>/
│                       └── <YYYY-MM-DD>.json   <- one raw Breeze daily candle dump
│
├── processed/
│   ├── parquet/                          <- canonical OHLCV, written by data_pipeline/build_processed_dataset.py
│   │   └── <SYMBOL>/
│   │       └── 1m/
│   │           └── <YYYY-MM>.parquet     <- one file per calendar month
│   │
│   └── features/                         <- derived features, never touches parquet/ above
│       └── vwap/                         <- written by features/vwap.py
│           └── <SYMBOL>/
│               └── 1m/
│                   └── <YYYY-MM>.parquet
│
└── validation_report_<SYMBOL>.json       <- written by data_pipeline/validate_raw_data.py
```

Processed monthly Parquet columns (see `config.CANONICAL_COLUMNS`):
`timestamp` (tz-aware, `Asia/Kolkata`), `symbol`, `exchange`, `open`, `high`,
`low`, `close`, `volume`.

Symbols currently on disk (as of last check): `RELIND`, `NIFTY`. There is
also a `NIFTY 50` folder under `raw/breeze/` (space in the name) alongside
`NIFTY` — looks like a stray/duplicate symbol folder, not confirmed which is
canonical. Worth checking with the user before relying on either if it comes
up.
