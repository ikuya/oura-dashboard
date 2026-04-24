"""Tests for db.py using an in-memory SQLite database."""

import db


# --- upsert_daily_metric / get_daily_metrics ---

def test_upsert_and_get_daily_metric(mem_conn):
    db.upsert_daily_metric(mem_conn, "sleep", "2024-01-01", 85, {"score": 85, "day": "2024-01-01"})
    mem_conn.commit()

    rows = db.get_daily_metrics(mem_conn, "sleep", "2024-01-01", "2024-01-01")
    assert len(rows) == 1
    assert rows[0]["day"] == "2024-01-01"
    assert rows[0]["score"] == 85


def test_upsert_replaces_existing_record(mem_conn):
    db.upsert_daily_metric(mem_conn, "sleep", "2024-01-01", 70, {"score": 70})
    db.upsert_daily_metric(mem_conn, "sleep", "2024-01-01", 90, {"score": 90})
    mem_conn.commit()

    rows = db.get_daily_metrics(mem_conn, "sleep", "2024-01-01", "2024-01-01")
    assert len(rows) == 1
    assert rows[0]["score"] == 90


def test_get_daily_metrics_respects_date_range(mem_conn):
    for day, score in [("2024-01-01", 70), ("2024-01-05", 80), ("2024-01-10", 90)]:
        db.upsert_daily_metric(mem_conn, "sleep", day, score, {"score": score})
    mem_conn.commit()

    rows = db.get_daily_metrics(mem_conn, "sleep", "2024-01-03", "2024-01-07")
    assert len(rows) == 1
    assert rows[0]["day"] == "2024-01-05"


def test_get_daily_metrics_returns_sorted(mem_conn):
    for day in ["2024-01-03", "2024-01-01", "2024-01-02"]:
        db.upsert_daily_metric(mem_conn, "readiness", day, 75, {"score": 75})
    mem_conn.commit()

    rows = db.get_daily_metrics(mem_conn, "readiness", "2024-01-01", "2024-01-03")
    days = [r["day"] for r in rows]
    assert days == sorted(days)


def test_get_daily_metrics_merges_json_fields(mem_conn):
    data = {"score": 80, "contributors": {"deep_sleep": 90}}
    db.upsert_daily_metric(mem_conn, "sleep", "2024-01-01", 80, data)
    mem_conn.commit()

    rows = db.get_daily_metrics(mem_conn, "sleep", "2024-01-01", "2024-01-01")
    assert rows[0]["contributors"] == {"deep_sleep": 90}


# --- upsert_heartrate_batch / get_heartrate ---

def test_upsert_heartrate_batch_inserts(mem_conn):
    records = [
        {"timestamp": "2024-01-01T00:00:00", "bpm": 60},
        {"timestamp": "2024-01-01T00:01:00", "bpm": 62},
    ]
    count = db.upsert_heartrate_batch(mem_conn, records)
    mem_conn.commit()

    assert count == 2
    rows = db.get_heartrate(mem_conn, "2024-01-01", "2024-01-01")
    assert len(rows) == 2


def test_upsert_heartrate_batch_ignores_duplicates(mem_conn):
    records = [{"timestamp": "2024-01-01T00:00:00", "bpm": 60}]
    db.upsert_heartrate_batch(mem_conn, records)
    db.upsert_heartrate_batch(mem_conn, records)
    mem_conn.commit()

    rows = db.get_heartrate(mem_conn, "2024-01-01", "2024-01-01")
    assert len(rows) == 1


def test_upsert_heartrate_batch_skips_invalid(mem_conn):
    records = [
        {"timestamp": "", "bpm": 60},
        {"timestamp": "2024-01-01T01:00:00", "bpm": None},
        {"timestamp": "2024-01-01T02:00:00", "bpm": 65},
    ]
    count = db.upsert_heartrate_batch(mem_conn, records)
    assert count == 1


def test_get_heartrate_respects_date_range(mem_conn):
    records = [
        {"timestamp": "2024-01-01T12:00:00", "bpm": 60},
        {"timestamp": "2024-01-05T12:00:00", "bpm": 65},
        {"timestamp": "2024-01-10T12:00:00", "bpm": 70},
    ]
    db.upsert_heartrate_batch(mem_conn, records)
    mem_conn.commit()

    rows = db.get_heartrate(mem_conn, "2024-01-03", "2024-01-07")
    assert len(rows) == 1
    assert rows[0]["bpm"] == 65


# --- sync_log ---

def test_update_and_get_sync_log(mem_conn):
    db.update_sync_log(mem_conn, "sleep", "2024-01-15")
    mem_conn.commit()

    assert db.get_last_synced_day(mem_conn, "sleep") == "2024-01-15"


def test_get_last_synced_day_returns_none_when_not_synced(mem_conn):
    assert db.get_last_synced_day(mem_conn, "sleep") is None


def test_update_sync_log_replaces_old_value(mem_conn):
    db.update_sync_log(mem_conn, "sleep", "2024-01-10")
    db.update_sync_log(mem_conn, "sleep", "2024-01-20")
    mem_conn.commit()

    assert db.get_last_synced_day(mem_conn, "sleep") == "2024-01-20"


# --- advice_history ---

def test_save_and_get_advice(mem_conn):
    with db.transaction(mem_conn):
        db.save_advice(mem_conn, "2024-01-01", "2024-01-14", "健康状態は良好です。")

    # get_advice_for_date looks up by saved_at date (today), not period_end
    from datetime import date
    today = date.today().isoformat()
    entry = db.get_advice_for_date(mem_conn, today)
    assert entry is not None
    assert entry["content"] == "健康状態は良好です。"
    assert entry["period_start"] == "2024-01-01"
    assert entry["period_end"] == "2024-01-14"


def test_get_advice_for_date_returns_none_when_missing(mem_conn):
    assert db.get_advice_for_date(mem_conn, "2024-01-01") is None


def test_get_advice_dates_groups_by_day(mem_conn):
    with db.transaction(mem_conn):
        mem_conn.execute(
            "INSERT INTO advice_history (saved_at, period_start, period_end, content) VALUES (?, ?, ?, ?)",
            ("2024-01-14T10:00:00+00:00", "2024-01-01", "2024-01-14", "advice A"),
        )
        mem_conn.execute(
            "INSERT INTO advice_history (saved_at, period_start, period_end, content) VALUES (?, ?, ?, ?)",
            ("2024-01-14T12:00:00+00:00", "2024-01-01", "2024-01-14", "advice B"),
        )
        mem_conn.execute(
            "INSERT INTO advice_history (saved_at, period_start, period_end, content) VALUES (?, ?, ?, ?)",
            ("2024-01-20T10:00:00+00:00", "2024-01-07", "2024-01-20", "advice C"),
        )

    dates = db.get_advice_dates(mem_conn)
    assert len(dates) == 2
    assert dates[0]["day"] == "2024-01-14"
    assert dates[1]["day"] == "2024-01-20"


# --- get_sync_status ---

def test_get_sync_status_all_metrics(mem_conn):
    status = db.get_sync_status(mem_conn)
    expected_metrics = {
        "sleep", "readiness", "activity", "stress", "spo2",
        "resilience", "cardiovascular_age", "vo2_max", "temperature", "heartrate",
    }
    assert set(status.keys()) == expected_metrics
    for v in status.values():
        assert "last_day" in v
        assert "rows" in v


def test_get_sync_status_counts_rows(mem_conn):
    db.upsert_daily_metric(mem_conn, "sleep", "2024-01-01", 80, {"score": 80})
    db.upsert_daily_metric(mem_conn, "sleep", "2024-01-02", 85, {"score": 85})
    db.update_sync_log(mem_conn, "sleep", "2024-01-02")
    mem_conn.commit()

    status = db.get_sync_status(mem_conn)
    assert status["sleep"]["rows"] == 2
    assert status["sleep"]["last_day"] == "2024-01-02"


# --- transaction rollback ---

def test_transaction_rolls_back_on_error(mem_conn):
    try:
        with db.transaction(mem_conn):
            db.upsert_daily_metric(mem_conn, "sleep", "2024-01-01", 80, {"score": 80})
            raise ValueError("intentional error")
    except ValueError:
        pass

    rows = db.get_daily_metrics(mem_conn, "sleep", "2024-01-01", "2024-01-01")
    assert len(rows) == 0
