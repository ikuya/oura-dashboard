# oura-dashboard

A local web dashboard for Oura Ring biometric data. Fetches data from the Oura Ring API v2 and stores it in a local SQLite database, so you can browse historical trends without hitting the API every time.

## Features

- Overview dashboard with Sleep, Readiness, Activity, Stress, SpO2, Temperature, Heart Rate, Resilience, VO2 Max, and Cardiovascular Age
- Incremental sync — only fetches dates not yet stored locally
- Date range selector: 7d / 30d / 90d / 180d
- **Advice** — analyzes the last 14 days of data with Claude and displays a health summary and personalized advice in Japanese
- Fully local: no external services beyond the Oura API and Claude Code

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

# Set your Oura token
echo "OURA_TOKEN=your_token_here" > .env
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

## API Endpoints

The Flask backend exposes a small JSON API used by the frontend.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/metrics` | Daily metrics for one or more types (`?metric=sleep,readiness,...&start=YYYY-MM-DD&end=YYYY-MM-DD`) |
| `GET` | `/api/metrics/<metric>` | Single metric detail |
| `GET` | `/api/heartrate` | Heart rate time series (`?start=YYYY-MM-DD&end=YYYY-MM-DD`) |
| `GET` | `/api/sync/status` | Last synced date and row count per metric |
| `POST` | `/api/sync` | Trigger incremental sync (body: `{"start": "...", "end": "..."}`) |
| `POST` | `/api/advice` | Analyze last 14 days with Claude and return health summary and advice |

## Project Structure

```
app.py           Flask app and route definitions
db.py            SQLite schema, upsert helpers, and query functions
sync.py          Incremental sync logic (Oura API → SQLite)
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
