# CLAUDE.md

Guidance for Claude Code working in this repo.

## Hard rules

- **Never open `.env`.** Not even to check a value, not even if asked
  indirectly. `.env.example` is safe to read.
- **Don't open `./data/`** unless the task explicitly requires reading a
  specific file in it — it's large and burns tokens fast. Its layout and
  schema are documented in `data_pipeline/data_structure.md`; read that
  instead.
- **Don't open `./logs/`** unless explicitly asked.
- **Don't open `./breeze_venv/`** unless explicitly asked.
- **Don't read `./todo.md`.** It's the user's personal scratchpad, not
  written for or by Claude — skip it even without being told.

## How the user works

- `prompt.txt` is how the user hands prompts to Claude in this project.

## Project layout

- **`./backtest/`** — all backtest code: the shared engine (`main.py`),
  `config.py` (which strategy/symbol/date range to run), and one folder per
  strategy (`strategy.py` + `reports/`).
- **`./data_loader.py`** — the one module backtest/research code should
  import to get market data. Currently a single file; will grow into its own
  module/folder as more data sources are added — don't be surprised if this
  changes.
- **`./data_pipeline/`** — download/validate/build code + its docs:
  `data_fetch_script.py`, `validate_raw_data.py`, `build_processed_dataset.py`,
  `run_pipeline.py` (validate -> build -> features, all in one), `config.py`
  (shared paths/constants), `data_fetch_script.md` (downloader spec/history),
  `data_structure.md` (`./data` layout reference). All paths anchor off
  `__file__` (repo root, one level up from `data_pipeline/`), not the
  current working directory, so these scripts run correctly regardless of
  where they're invoked from.
- **`./instrument_master.py`**, **`./breeze_test.py`** — standalone
  root-level utility scripts (security master download, auth smoke test),
  not part of the pipeline proper.
- **`./features/`** — derived features computed from processed OHLCV
  (currently just VWAP in `vwap.py`). More features will be added here over
  time; all feature output goes under `data/processed/features/`.
- **`./data/`** — raw + processed data and all features (raw/processed
  always lands here). See the hard rule above; don't open directly.
- **`./plot/`** — known broken / out of scope. Leave it alone unless
  explicitly asked to work on it.

## Docs

- **`runbook.md`** — how to download data and run a backtest, step by step.
- **`README.md`** — data pipeline architecture, stage by stage (validate ->
  build -> load -> features -> backtest).
- **`backtest/STRATEGY_GUIDE.md`** — how to write a new strategy.
- **`data_pipeline/data_fetch_script.md`** — full spec, known-working
  auth flow, and bug history for the Breeze downloader.
