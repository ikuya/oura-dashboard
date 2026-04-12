"""Incremental sync: fetch from Oura API and write to local SQLite DB."""

import os
from datetime import date, timedelta

from dotenv import load_dotenv

import db
from oura_client import OuraClient, OuraAPIError, _today_str

load_dotenv()

DEFAULT_START = "2020-01-01"
RESILIENCE_LEVEL_ORDER = {
    "limited": 1,
    "adequate": 2,
    "solid": 3,
    "strong": 4,
    "exceptional": 5,
}


def find_missing_range(conn, metric: str, requested_end: str) -> tuple[str, str] | None:
    """Return (start, end) date range to fetch, or None if already synced."""
    last = db.get_last_synced_day(conn, metric)
    today = _today_str()
    end = min(requested_end, today)

    if last is None:
        return (DEFAULT_START, end)

    # Always re-fetch today since data updates throughout the day
    if last == today and end == today:
        return (today, today)

    next_day = (date.fromisoformat(last) + timedelta(days=1)).isoformat()
    if next_day > end:
        return None

    return (next_day, end)


def _extract_score(metric: str, record: dict):
    """Extract a scalar score value from a raw API record."""
    if metric == "sleep":
        return record.get("score")
    elif metric == "readiness":
        return record.get("score")
    elif metric == "activity":
        return record.get("score")
    elif metric == "stress":
        return record.get("stress_high")
    elif metric == "spo2":
        spo2 = record.get("spo2_percentage")
        if isinstance(spo2, dict):
            return spo2.get("average")
        return spo2
    elif metric == "resilience":
        level = record.get("level", "")
        return RESILIENCE_LEVEL_ORDER.get(level)
    elif metric == "cardiovascular_age":
        return record.get("vascular_age")
    elif metric == "vo2_max":
        return record.get("vo2_max")
    elif metric == "temperature":
        return record.get("temperature_deviation")
    return None


def sync_daily_metric(conn, client: OuraClient, metric: str, start: str, end: str) -> int:
    """Fetch and upsert daily metric records. Returns number of rows written."""
    fetch_map = {
        "sleep": client.get_daily_sleep,
        "readiness": client.get_daily_readiness,
        "activity": client.get_daily_activity,
        "stress": client.get_daily_stress,
        "spo2": client.get_daily_spo2,
        "resilience": client.get_daily_resilience,
        "cardiovascular_age": client.get_daily_cardiovascular_age,
        "vo2_max": client.get_vo2_max,
    }

    fetch_fn = fetch_map[metric]
    records = fetch_fn(start, end)

    count = 0
    for r in records:
        day = r.get("day")
        if not day:
            continue
        score = _extract_score(metric, r)
        db.upsert_daily_metric(conn, metric, day, score, r)
        count += 1

    # Extract temperature from readiness if syncing readiness
    if metric == "readiness" and records:
        for r in records:
            day = r.get("day")
            if not day:
                continue
            temp_record = {
                "day": day,
                "temperature_deviation": r.get("temperature_deviation"),
                "temperature_trend_deviation": r.get("temperature_trend_deviation"),
                "body_temperature_score": r.get("contributors", {}).get("body_temperature"),
            }
            score = r.get("temperature_deviation")
            db.upsert_daily_metric(conn, "temperature", day, score, temp_record)
        db.update_sync_log(conn, "temperature", end)

    return count


def sync_heartrate(conn, client: OuraClient, start: str, end: str) -> int:
    records = client.get_heartrate(start, end)
    count = db.upsert_heartrate_batch(conn, records)
    return count


def run_sync(
    conn,
    client: OuraClient,
    requested_start: str | None = None,
    requested_end: str | None = None,
    metrics: list[str] | None = None,
) -> dict:
    """Run incremental sync for all (or specified) metrics. Returns summary dict."""
    today = _today_str()
    end = requested_end or today

    all_daily_metrics = ["sleep", "readiness", "activity", "stress", "spo2",
                         "resilience", "cardiovascular_age", "vo2_max"]
    if metrics is None:
        target_metrics = all_daily_metrics + ["heartrate"]
    else:
        target_metrics = metrics

    result = {"synced": {}, "errors": {}}

    for metric in target_metrics:
        if metric == "temperature":
            # Temperature is derived from readiness; skip explicit sync
            continue

        rng = find_missing_range(conn, metric, end)
        if requested_start:
            # Override: use the explicitly requested start
            rng = (requested_start, end)

        if rng is None:
            result["synced"][metric] = 0
            continue

        fetch_start, fetch_end = rng
        if metric == "heartrate":
            # Heartrate API enforces a max 30-day window
            max_start = (date.fromisoformat(fetch_end) - timedelta(days=29)).isoformat()
            fetch_start = max(fetch_start, max_start)
        try:
            with db.transaction(conn):
                if metric == "heartrate":
                    count = sync_heartrate(conn, client, fetch_start, fetch_end)
                else:
                    count = sync_daily_metric(conn, client, metric, fetch_start, fetch_end)
                db.update_sync_log(conn, metric, fetch_end)
            result["synced"][metric] = count
        except OuraAPIError as e:
            result["errors"][metric] = e.message

    return result


if __name__ == "__main__":
    token = os.environ.get("OURA_TOKEN")
    if not token:
        print("OURA_TOKEN not set")
        raise SystemExit(1)

    db.init_db()
    client = OuraClient(token)
    with db.get_connection() as conn:
        summary = run_sync(conn, client)

    print("Sync complete:")
    for metric, count in summary["synced"].items():
        print(f"  {metric}: {count} rows")
    if summary["errors"]:
        print("Errors:")
        for metric, msg in summary["errors"].items():
            print(f"  {metric}: {msg}")
