"""Tests for sync.py (OuraClient is mocked)."""

from unittest.mock import MagicMock, patch

import pytest

import db
import sync
from sync import find_missing_range, _extract_score, sync_daily_metric, run_sync, _backfill_ranges


# --- find_missing_range ---

def test_find_missing_range_no_history(mem_conn):
    start, end = find_missing_range(mem_conn, "sleep", "2024-01-31")
    assert start == sync.DEFAULT_START
    assert end == "2024-01-31"


def test_find_missing_range_incremental(mem_conn):
    db.update_sync_log(mem_conn, "sleep", "2024-01-10")
    mem_conn.commit()

    with patch("sync._today_str", return_value="2024-01-31"):
        start, end = find_missing_range(mem_conn, "sleep", "2024-01-31")

    assert start == "2024-01-11"
    assert end == "2024-01-31"


def test_find_missing_range_already_up_to_date(mem_conn):
    db.update_sync_log(mem_conn, "sleep", "2024-01-31")
    mem_conn.commit()

    with patch("sync._today_str", return_value="2024-01-31"):
        result = find_missing_range(mem_conn, "sleep", "2024-01-30")

    assert result is None


def test_find_missing_range_today_refetch(mem_conn):
    with patch("sync._today_str", return_value="2024-01-31"):
        db.update_sync_log(mem_conn, "sleep", "2024-01-31")
        mem_conn.commit()
        start, end = find_missing_range(mem_conn, "sleep", "2024-01-31")

    assert start == "2024-01-31"
    assert end == "2024-01-31"


def test_find_missing_range_caps_at_today(mem_conn):
    with patch("sync._today_str", return_value="2024-01-15"):
        start, end = find_missing_range(mem_conn, "sleep", "2024-01-31")

    assert end == "2024-01-15"


# --- _extract_score ---

@pytest.mark.parametrize("metric,record,expected", [
    ("sleep", {"score": 85}, 85),
    ("readiness", {"score": 72}, 72),
    ("activity", {"score": 60}, 60),
    ("stress", {"stress_high": 5000}, 5000),
    ("spo2", {"spo2_percentage": {"average": 98.5}}, 98.5),
    ("spo2", {"spo2_percentage": 97.0}, 97.0),
    ("resilience", {"level": "solid"}, 3),
    ("resilience", {"level": "exceptional"}, 5),
    ("resilience", {"level": "unknown"}, None),
    ("cardiovascular_age", {"vascular_age": 35}, 35),
    ("temperature", {"temperature_deviation": 0.2}, 0.2),
    ("heartrate", {"bpm": 60}, None),
])
def test_extract_score(metric, record, expected):
    assert _extract_score(metric, record) == expected


# --- sync_daily_metric ---

def _make_client(metric, records):
    client = MagicMock()
    fetch_map = {
        "sleep": client.get_daily_sleep,
        "readiness": client.get_daily_readiness,
        "activity": client.get_daily_activity,
        "stress": client.get_daily_stress,
        "spo2": client.get_daily_spo2,
        "resilience": client.get_daily_resilience,
        "cardiovascular_age": client.get_daily_cardiovascular_age,
    }
    fetch_map[metric].return_value = records
    return client


def test_sync_daily_metric_writes_records(mem_conn):
    records = [
        {"day": "2024-01-01", "score": 80},
        {"day": "2024-01-02", "score": 85},
    ]
    client = _make_client("sleep", records)

    count = sync_daily_metric(mem_conn, client, "sleep", "2024-01-01", "2024-01-02")
    mem_conn.commit()

    assert count == 2
    rows = db.get_daily_metrics(mem_conn, "sleep", "2024-01-01", "2024-01-02")
    assert len(rows) == 2


def test_sync_daily_metric_skips_records_without_day(mem_conn):
    records = [{"score": 80}, {"day": "2024-01-02", "score": 85}]
    client = _make_client("sleep", records)

    count = sync_daily_metric(mem_conn, client, "sleep", "2024-01-01", "2024-01-02")
    mem_conn.commit()

    assert count == 1


def test_sync_daily_metric_extracts_temperature_from_readiness(mem_conn):
    records = [{
        "day": "2024-01-01",
        "score": 75,
        "temperature_deviation": 0.3,
        "temperature_trend_deviation": 0.1,
        "contributors": {"body_temperature": 80},
    }]
    client = _make_client("readiness", records)

    sync_daily_metric(mem_conn, client, "readiness", "2024-01-01", "2024-01-01")
    mem_conn.commit()

    temp_rows = db.get_daily_metrics(mem_conn, "temperature", "2024-01-01", "2024-01-01")
    assert len(temp_rows) == 1
    assert temp_rows[0]["temperature_deviation"] == 0.3


# --- run_sync ---

def _make_full_client(daily_records=None, heartrate_records=None):
    client = MagicMock()
    records = daily_records or []
    client.get_daily_sleep.return_value = records
    client.get_daily_readiness.return_value = records
    client.get_daily_activity.return_value = records
    client.get_daily_stress.return_value = records
    client.get_daily_spo2.return_value = records
    client.get_daily_resilience.return_value = records
    client.get_daily_cardiovascular_age.return_value = records
    client.get_heartrate.return_value = heartrate_records or []
    return client


def test_run_sync_returns_summary(mem_conn):
    with patch("sync._today_str", return_value="2024-01-31"):
        client = _make_full_client()
        result = run_sync(mem_conn, client)

    assert "synced" in result
    assert "errors" in result


def test_run_sync_skips_temperature_metric(mem_conn):
    with patch("sync._today_str", return_value="2024-01-31"):
        client = _make_full_client()
        result = run_sync(mem_conn, client, metrics=["temperature", "sleep"])

    assert "temperature" not in result["synced"]
    assert "sleep" in result["synced"]


def test_run_sync_requested_start_overrides_incremental(mem_conn):
    db.update_sync_log(mem_conn, "sleep", "2024-01-20")
    mem_conn.commit()

    with patch("sync._today_str", return_value="2024-01-31"):
        client = _make_full_client(
            daily_records=[{"day": "2024-01-10", "score": 80}]
        )
        run_sync(mem_conn, client, requested_start="2024-01-10", metrics=["sleep"])

    client.get_daily_sleep.assert_called_once_with("2024-01-10", "2024-01-31")


def test_run_sync_captures_api_errors(mem_conn):
    from oura_client import OuraAPIError

    client = _make_full_client()
    client.get_daily_sleep.side_effect = OuraAPIError(401, "Unauthorized")

    with patch("sync._today_str", return_value="2024-01-31"):
        result = run_sync(mem_conn, client, metrics=["sleep"])

    assert "sleep" in result["errors"]
    # On error the synced count is 0 (not omitted)
    assert result["synced"].get("sleep", 0) == 0


def test_run_sync_heartrate_window_capped_at_30_days(mem_conn):
    with patch("sync._today_str", return_value="2024-01-31"):
        client = _make_full_client()
        run_sync(mem_conn, client, metrics=["heartrate"])

    # Each API call must stay within a 30-day window
    for call in client.get_heartrate.call_args_list:
        start_arg, end_arg = call[0]
        from datetime import date
        delta = date.fromisoformat(end_arg) - date.fromisoformat(start_arg)
        assert delta.days <= 29


def test_run_sync_heartrate_loops_to_cover_full_range(mem_conn):
    # Sync history starts from DEFAULT_START; today is 60+ days later so multiple windows needed
    with patch("sync._today_str", return_value="2024-03-31"):
        client = _make_full_client()
        run_sync(mem_conn, client, metrics=["heartrate"])

    # Should have been called more than once to cover the full range
    assert client.get_heartrate.call_count > 1
    # First call should cover the most recent window
    first_call_end = client.get_heartrate.call_args_list[0][0][1]
    assert first_call_end == "2024-03-31"


# --- _backfill_ranges ---

def test_backfill_ranges_all_missing(mem_conn):
    # No rows in DB → entire window is a single gap
    ranges = _backfill_ranges(mem_conn, "sleep", 7, "2024-01-10")
    assert ranges == [("2024-01-04", "2024-01-10")]


def test_backfill_ranges_no_gaps(mem_conn):
    for i in range(7):
        from datetime import date, timedelta
        day = (date.fromisoformat("2024-01-10") - timedelta(days=6 - i)).isoformat()
        db.upsert_daily_metric(mem_conn, "sleep", day, 80, {"day": day})
    mem_conn.commit()

    ranges = _backfill_ranges(mem_conn, "sleep", 7, "2024-01-10")
    assert ranges == [("2024-01-10", "2024-01-10")]


def test_backfill_ranges_single_gap_in_middle(mem_conn):
    from datetime import date, timedelta
    # Insert all days except 2024-01-07 and 2024-01-08
    for i in range(7):
        day = (date.fromisoformat("2024-01-10") - timedelta(days=6 - i)).isoformat()
        if day in ("2024-01-07", "2024-01-08"):
            continue
        db.upsert_daily_metric(mem_conn, "sleep", day, 80, {"day": day})
    mem_conn.commit()

    ranges = _backfill_ranges(mem_conn, "sleep", 7, "2024-01-10")
    assert ranges == [("2024-01-07", "2024-01-08"), ("2024-01-10", "2024-01-10")]


def test_backfill_ranges_heartrate(mem_conn):
    # Insert heartrate for all days except 2024-01-09
    from datetime import date, timedelta
    for i in range(7):
        day = (date.fromisoformat("2024-01-10") - timedelta(days=6 - i)).isoformat()
        if day == "2024-01-09":
            continue
        mem_conn.execute(
            "INSERT INTO heartrate (timestamp, bpm, day) VALUES (?, ?, ?)",
            (f"{day}T00:00:00", 60, day),
        )
    mem_conn.commit()

    ranges = _backfill_ranges(mem_conn, "heartrate", 7, "2024-01-10")
    assert ranges == [("2024-01-09", "2024-01-10")]


# --- run_sync with backfill ---

def test_run_sync_backfill_fetches_missing_days(mem_conn):
    from datetime import date, timedelta

    today = "2024-01-10"
    # Insert all days within window except 2024-01-08 (gap 2 days ago)
    # sync_log set to yesterday so today's incremental range is (today, today),
    # not covering the gap.
    yesterday = "2024-01-09"
    for i in range(7):
        day = (date.fromisoformat(today) - timedelta(days=6 - i)).isoformat()
        if day == "2024-01-08":
            continue
        db.upsert_daily_metric(mem_conn, "sleep", day, 80, {"day": day})
    db.update_sync_log(mem_conn, "sleep", yesterday)
    mem_conn.commit()

    client = _make_full_client(
        daily_records=[{"day": "2024-01-08", "score": 75}, {"day": "2024-01-10", "score": 80}]
    )

    with patch("sync._today_str", return_value=today):
        result = run_sync(mem_conn, client, metrics=["sleep"], backfill_days=7)

    called_ranges = [c[0] for c in client.get_daily_sleep.call_args_list]
    # Incremental call: (today, today) and backfill gap: (2024-01-08, 2024-01-08)
    assert ("2024-01-10", "2024-01-10") in called_ranges
    assert ("2024-01-08", "2024-01-08") in called_ranges
    assert result["synced"]["sleep"] > 0


def test_run_sync_no_backfill_skips_gaps(mem_conn):
    from datetime import date, timedelta

    today = "2024-01-10"
    yesterday = "2024-01-09"
    # Insert all days within window except 2024-01-08
    for i in range(7):
        day = (date.fromisoformat(today) - timedelta(days=6 - i)).isoformat()
        if day == "2024-01-08":
            continue
        db.upsert_daily_metric(mem_conn, "sleep", day, 80, {"day": day})
    db.update_sync_log(mem_conn, "sleep", yesterday)
    mem_conn.commit()

    client = _make_full_client(
        daily_records=[{"day": "2024-01-10", "score": 80}]
    )

    with patch("sync._today_str", return_value=today):
        result = run_sync(mem_conn, client, metrics=["sleep"])

    # Without backfill, only the incremental call (today, today) should happen
    client.get_daily_sleep.assert_called_once_with("2024-01-10", "2024-01-10")
    # Gap on 2024-01-08 should NOT be fetched
    called_ranges = [c[0] for c in client.get_daily_sleep.call_args_list]
    assert ("2024-01-08", "2024-01-08") not in called_ranges
