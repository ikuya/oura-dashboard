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


REFETCH_DAYS = 7  # Re-fetch recent days since Oura may update scores retroactively


def find_missing_range(conn, metric: str, requested_end: str) -> tuple[str, str] | None:
    """Return (start, end) date range to fetch, or None if already synced."""
    last = db.get_last_synced_day(conn, metric)
    today = _today_str()
    end = min(requested_end, today)

    if last is None:
        return (DEFAULT_START, end)

    # Always re-fetch the last REFETCH_DAYS days because Oura scores (especially
    # activity) can be null initially and get populated hours later.
    refetch_start = (date.fromisoformat(today) - timedelta(days=REFETCH_DAYS - 1)).isoformat()
    next_day = (date.fromisoformat(last) + timedelta(days=1)).isoformat()

    # Start from whichever is earlier: the refetch window or the next unsynced day
    fetch_start = min(refetch_start, next_day)
    if fetch_start > end:
        return None

    return (fetch_start, end)


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


def _backfill_ranges(conn, metric: str, backfill_days: int, today: str) -> list[tuple[str, str]]:
    """Return date ranges needed to fill gaps in the backfill window.

    Checks each day in [today-backfill_days+1 .. today] for missing DB rows and
    returns the minimal set of contiguous ranges that cover those gaps.

    For heartrate, day-level presence checks are insufficient because partial-day
    gaps (e.g. a ring removed mid-day) won't be detected. The entire window is
    always returned so INSERT OR IGNORE fills any holes without creating duplicates.
    """
    window_start = (date.fromisoformat(today) - timedelta(days=backfill_days - 1)).isoformat()

    if metric == "heartrate":
        return [(window_start, today)]

    # Treat days with null score as missing so they get re-fetched.
    # Oura may return score=null initially and populate it hours later.
    existing_days = {
        row[0]
        for row in conn.execute(
            "SELECT day FROM daily_metrics WHERE metric = ? AND day >= ? AND day <= ? AND score IS NOT NULL",
            (metric, window_start, today),
        ).fetchall()
    }

    ranges: list[tuple[str, str]] = []
    gap_start: str | None = None
    current = date.fromisoformat(window_start)
    end_date = date.fromisoformat(today)

    while current <= end_date:
        day_str = current.isoformat()
        missing = day_str not in existing_days or day_str == today
        if missing and gap_start is None:
            gap_start = day_str
        elif not missing and gap_start is not None:
            ranges.append((gap_start, (current - timedelta(days=1)).isoformat()))
            gap_start = None
        current += timedelta(days=1)

    if gap_start is not None:
        ranges.append((gap_start, today))

    return ranges


def run_sync(
    conn,
    client: OuraClient,
    requested_start: str | None = None,
    requested_end: str | None = None,
    metrics: list[str] | None = None,
    backfill_days: int = 0,
) -> dict:
    """Run incremental sync for all (or specified) metrics. Returns summary dict.

    If backfill_days > 0, any missing days within the last backfill_days window
    are also fetched regardless of the sync log state.
    """
    today = _today_str()
    end = requested_end or today

    all_daily_metrics = ["sleep", "readiness", "activity", "stress", "spo2",
                         "resilience", "cardiovascular_age"]
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

        # Collect ranges to fetch: incremental range + any backfill gaps
        ranges_to_fetch: list[tuple[str, str]] = []
        backfill_ranges: list[tuple[str, str]] = []
        if backfill_days > 0 and not requested_start:
            backfill_ranges = _backfill_ranges(conn, metric, backfill_days, today)

        if rng is not None:
            # Skip incremental range if it's already covered by a backfill range
            covered = any(b[0] <= rng[0] and b[1] >= rng[1] for b in backfill_ranges)
            if not covered:
                ranges_to_fetch.append(rng)

        for gap in backfill_ranges:
            ranges_to_fetch.append(gap)

        if not ranges_to_fetch:
            result["synced"][metric] = 0
            continue

        if metric == "heartrate":
            total = 0
            for fetch_start, fetch_end in ranges_to_fetch:
                # Heartrate API enforces a max 30-day window; loop backwards to cover full range
                window_end = fetch_end
                try:
                    while True:
                        window_start = max(
                            fetch_start,
                            (date.fromisoformat(window_end) - timedelta(days=29)).isoformat(),
                        )
                        with db.transaction(conn):
                            total += sync_heartrate(conn, client, window_start, window_end)
                            db.update_sync_log(conn, metric, fetch_end)
                        if window_start <= fetch_start:
                            break
                        window_end = (date.fromisoformat(window_start) - timedelta(days=1)).isoformat()
                except OuraAPIError as e:
                    result["errors"][metric] = e.message
                    break
            result["synced"][metric] = total
            continue

        total = 0
        for fetch_start, fetch_end in ranges_to_fetch:
            try:
                with db.transaction(conn):
                    count = sync_daily_metric(conn, client, metric, fetch_start, fetch_end)
                    db.update_sync_log(conn, metric, fetch_end)
                total += count
            except OuraAPIError as e:
                result["errors"][metric] = e.message
                break
        result["synced"][metric] = total

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
