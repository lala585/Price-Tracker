# RV Price & Inventory Tracker & Local Search


This repository contains two Python automation scripts that monitor RV listing inventory and price movement across dealer websites and RV marketplaces:

- `rv_price_tracker/rv_tracker_playwright.py` scans regional dealer inventory and writes the main private database at `rv_price_tracker/rv_tracker.db`.
- `rv_trader_search/rv_trader_search.py` searches RVTrader and related marketplace-style inventory pages and writes the tracker database at `rv_trader_search/rvtrader_tracker.db`.

Both scripts persist listing snapshots and price history in SQLite and optionally send Discord webhooks for newly discovered listings and price reductions.

## Repository layout

```text
README.md
rv_price_tracker/
    rv_tracker_playwright.py
rv_trader_search/
    rv_trader_search.py
.github/workflows/
    rv_price_tracker.yml
    rv_trader_tracker.yml
```

## What each tracker does

### RV price tracker

The main tracker in `rv_price_tracker/rv_tracker_playwright.py` is a multi-dealer inventory scraper configured around a `MODELS_CATALOG` and a `DEALER_TEMPLATES` map for Dealer Spike and NetSource-style inventory pages. The file also contains `STEALTH_SUITE_DEALERS` and a set of browser/request timing controls for crawling the configured sources.

The script records:

- listing title
- model year
- condition
- source ID or fallback hash-derived source ID
- price
- listing URL
- listing source and seller location

It also stores persistent metadata in SQLite and creates price history rows as the listing changes.

### RVTrader search tracker

The search tracker in `rv_trader_search/rv_trader_search.py` focuses on RVTrader search URL builders and marketplace discovery patterns. It uses the budgeted `SEARCH_ZIP`, `SEARCH_RADIUS`, model key catalog, and `MODELS_CATALOG` data to search multiple sources while keeping the process throttled and deterministic.

The script writes records to `rv_trader_search/rvtrader_tracker.db` and includes the price-history table pattern used by the main codebase.

## Features

- Model-specific search across multiple dealer or marketplace templates.
- Batched execution and batching cooldowns.
- Normalized price extraction and price-history maintenance.
- SQLite persistence for current listings and historical prices.
- Deterministic SHA-256 fallback IDs when a listing has no stable source number.
- Discord webhook integration for new inventory and price reductions.
- Workflow support for GitHub Actions automation and private database sync through Google Cloud Storage.

## Requirements

- Windows, macOS, or Linux
- Python 3.10+
- Playwright Chromium runtime
- Internet access to the configured inventory websites and marketplaces

## Local setup

Create and activate a project virtual environment from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
python -m pip install --upgrade pip
python -m pip install playwright requests beautifulsoup4
```

Install the Playwright Chromium runtime:

```powershell
python -m playwright install chromium
```

## Running locally

### Main RV price tracker

```powershell
python .\rv_price_tracker\rv_tracker_playwright.py
```

The main script writes progress, parser output, HTTP errors, and the final summary to the console. On the first run it creates the local database `rv_price_tracker/rv_tracker.db`.

### RVTrader search tracker

```powershell
python .\rv_trader_search\rv_trader_search.py
```

The RVTrader tracker writes to `rv_trader_search/rvtrader_tracker.db`.

## Configuration

The main tracker exposes configuration constants at the top of `rv_price_tracker/rv_tracker_playwright.py`:

- `MIN_MODEL_YEAR` and `MAX_MODEL_YEAR` define the accepted model-year range.
- `BATCH_SIZE` controls how many models are processed in a browser context.
- `BATCH_COOLDOWN_SEC` controls the cooldown between batches.
- `DEALER_TEMPLATES` contains the URL templates for dynamic dealer search pages.
- `STEALTH_SUITE_DEALERS` contains the extra partner inventory sites for the Stealth / Coast Technology mapping.
- `MODELS_CATALOG` contains the tracked model definitions for the catalog.

The RVTrader search script has its own matching constants in `rv_trader_search/rv_trader_search.py`, including `SEARCH_ZIP`, `SEARCH_RADIUS`, `BATCH_SIZE`, `MIN_QUERY_DELAY`, `MAX_QUERY_DELAY`, and `MODELS_CATALOG`.

To enable Discord notifications, set the environment variable in the same shell session that launches the script:

```powershell
$env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."
```

## GitHub Actions workflows

The repository includes two CI workflow files:

- `.github/workflows/rv_price_tracker.yml` runs the main price tracker on a scheduled cron and via manual dispatch.
- `.github/workflows/rv_trader_tracker.yml` runs the RVTrader search tracker twice daily and supports manual triggering through the Actions tab.

Both workflows authenticate to Google Cloud Storage, download a private database file if it exists, run the corresponding Python script, and upload the updated database back to the same bucket.

## Database schema

Both trackers are built around the same SQLite scheme pattern:

- `listings` contains the current known unit details and most recent timestamps.
- `price_history` contains historical prices for each source and source ID pair.

Because the trackers are bringing in third-party inventory information, the local database should be treated as a private and recoverable artifact.

## Troubleshooting

### No results are returned

- Check that the target website or marketplace can be reached in a regular browser session.
- Review console output for HTTP errors or parser failures.
- Confirm that the model-year filter and model keys align with the catalog you want to monitor.

### Playwright cannot launch

Re-run the browser installation command:

```powershell
python -m playwright install chromium
```

### Discord notifications are not sent

Make sure `DISCORD_WEBHOOK_URL` is defined in the same terminal session that starts the Python process. The tracker code checks for that environment variable before any Discord request is made.

## Notes

This project depends on the availability, layout, and site policies of a set of public and private inventory websites. Respect listings, robots.txt expectations, and rate limits where appropriate, and keep the database backed up if historical price data matters.