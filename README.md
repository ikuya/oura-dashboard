# oura-dashboard

A local web dashboard for Oura Ring biometric data. Fetches data from the Oura Ring API v2 and stores it in a local SQLite database, so you can browse historical trends without hitting the API every time.

## Features

- Overview dashboard with Sleep (including Restfulness trend), Readiness, Activity, Stress, SpO2, Temperature, Heart Rate, Resilience, VO2 Max, and Cardiovascular Age
- Incremental sync — only fetches dates not yet stored locally
- Date range selector: 7d / 30d / 90d / 180d
- **Advice** — analyzes the last 14 days of data with Claude and displays a health summary and personalized advice in Japanese
- **Advice History** — past advice is saved to the database and browsable via a compact calendar UI
- **Password protection** — the dashboard is protected by a configurable password (set via `APP_PASSWORD` in `.env`)
- Fully local: no external services beyond the Oura API and Claude API

## Getting an Access Token

1. Visit https://cloud.ouraring.com/personal-access-tokens (login required)
2. Click **"Create A New Personal Access Token"**
3. Enter a name (e.g. `oura-dashboard`)
4. Click **"Create"**
5. Copy the displayed token (it will not be shown again)

> **Note:** API access requires an active Oura Membership.

## Setup

```bash
# Install dependencies
uv sync

# Set required environment variables
echo "OURA_TOKEN=your_token_here" > .env
echo "SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')" >> .env
echo "APP_PASSWORD=your_password_here" >> .env
```

The **Advice** feature calls `claude` CLI via subprocess. Log in with your Claude Code subscription before using it:

```bash
claude auth login
```

## Running

```bash
uv run flask --app app run
```

Open http://localhost:5000 in a browser.

On first launch, click **Sync** to fetch your historical data from the Oura API. Subsequent syncs only fetch data newer than the last sync.

## Daily Automatic Sync

`daily_sync.py` runs an incremental sync and backfills any missing days within the last 7 days in one shot:

```bash
uv run python daily_sync.py
```

### cron

Add a crontab entry with `crontab -e`:

```
0 0,6,12,18 * * * /path/to/oura-dashboard/run_daily_sync.sh
```

This runs 4 times a day (midnight, 6 AM, noon, 6 PM) so intraday heart rate data stays up to date.

`.env` はプロジェクトディレクトリに置いておけば `python-dotenv` が自動で読み込みます。

## API Endpoints

The Flask backend exposes a small JSON API used by the frontend.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/metrics` | Daily metrics for one or more types (`?metric=sleep,readiness,...&start=YYYY-MM-DD&end=YYYY-MM-DD`) |
| `GET` | `/api/metrics/<metric>` | Single metric detail |
| `GET` | `/api/heartrate` | Heart rate time series (`?start=YYYY-MM-DD&end=YYYY-MM-DD`) |
| `GET` | `/api/sync/status` | Last synced date and row count per metric |
| `POST` | `/api/sync` | Trigger incremental sync (body: `{"start": "...", "end": "..."}`) |
| `POST` | `/api/advice` | Start advice analysis job for the last 14 days and return a `job_id` |
| `GET` | `/api/advice/<job_id>` | Poll advice job status and get the completed advice |
| `GET` | `/api/advice/history` | List of dates for which saved advice exists |
| `GET` | `/api/advice/history/<YYYY-MM-DD>` | Retrieve saved advice for a specific date |

## Project Structure

```
app.py           Flask app and route definitions
db.py            SQLite schema, upsert helpers, and query functions
sync.py          Incremental sync logic (Oura API → SQLite)
daily_sync.py    CLI entry point for daily automated sync (with 7-day backfill)
oura_client.py   Oura Ring API v2 HTTP client
static/
  index.html     Dashboard HTML
  dashboard.js   Chart.js rendering and API calls
  style.css      Layout and theme
oura.db          Local SQLite database (created on first run)
```

## Data Notes

- **Temperature** has no dedicated API endpoint. It is derived from the readiness endpoint and stored separately during sync.
- **Heart rate** is stored at 5-minute resolution in a separate table. The dashboard displays the last 7 days regardless of the selected date range to avoid rendering thousands of points.
- **Resilience** level is a categorical value (`limited` / `adequate` / `solid` / `strong` / `exceptional`), mapped to an ordinal for charting.
- **VO2 Max** and **Cardiovascular Age** update infrequently; gaps are bridged with `spanGaps` in the chart.
