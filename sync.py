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


def find_gaps(conn, metric: str, requested_end: str) -> list[tuple[str, str]]:
    """Return list of (start, end) date ranges missing from DB, plus today for re-fetch."""
    today = _today_str()
    end = min(requested_end, today)
    end_date = date.fromisoformat(end)
    start_date = date.fromisoformat(DEFAULT_START)

    rows = conn.execute(
        "SELECT day FROM daily_metrics WHERE metric = ? AND day <= ? ORDER BY day",
        (metric, end),
    ).fetchall()
    existing = {row["day"] for row in rows}

    gaps = []
    gap_start = None
    gap_end = None
    current = start_date
    while current <= end_date:
        day_str = current.isoformat()
        if day_str not in existing:
            if gap_start is None:
                gap_start = day_str
            gap_end = day_str
        else:
            if gap_start is not None:
                gaps.append((gap_start, gap_end))
                gap_start = None
                gap_end = None
        current += timedelta(days=1)
    if gap_start is not None:
        gaps.append((gap_start, end))

    # Always re-fetch today since data updates throughout the day
    if not gaps or gaps[-1][1] != today:
        gaps.append((today, today))

    return gaps


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

        if requested_start:
            gaps = [(requested_start, end)]
        elif metric == "heartrate":
            # Heartrate: use last-synced-day based range (DB scan is too large)
            last = db.get_last_synced_day(conn, metric)
            if last is None:
                gaps = [(DEFAULT_START, end)]
            elif last == end:
                gaps = [(end, end)]
            else:
                next_day = (date.fromisoformat(last) + timedelta(days=1)).isoformat()
                gaps = [(next_day, end)] if next_day <= end else [(end, end)]
        else:
            gaps = find_gaps(conn, metric, end)

        total_count = 0
        for fetch_start, fetch_end in gaps:
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
                total_count += count
            except OuraAPIError as e:
                result["errors"][metric] = e.message
                break
        result["synced"][metric] = total_count

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
