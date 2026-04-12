"""SQLite database layer: schema, upsert helpers, and query functions."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "oura.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS daily_metrics (
                id          INTEGER PRIMARY KEY,
                metric      TEXT NOT NULL,
                day         TEXT NOT NULL,
                score       REAL,
                data_json   TEXT NOT NULL,
                synced_at   TEXT NOT NULL,
                UNIQUE(metric, day)
            );

            CREATE TABLE IF NOT EXISTS heartrate (
                id          INTEGER PRIMARY KEY,
                timestamp   TEXT NOT NULL UNIQUE,
                bpm         INTEGER NOT NULL,
                day         TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_heartrate_day ON heartrate(day);

            CREATE TABLE IF NOT EXISTS sync_log (
                metric          TEXT PRIMARY KEY,
                last_synced_day TEXT NOT NULL
            );
        """)


def upsert_daily_metric(conn: sqlite3.Connection, metric: str, day: str, score, data: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO daily_metrics (metric, day, score, data_json, synced_at)
           VALUES (?, ?, ?, ?, ?)""",
        (metric, day, score, json.dumps(data, ensure_ascii=False), now),
    )


def upsert_heartrate_batch(conn: sqlite3.Connection, records: list[dict]) -> int:
    inserted = 0
    for r in records:
        ts = r.get("timestamp", "")
        bpm = r.get("bpm")
        if not ts or bpm is None:
            continue
        day = ts[:10]
        conn.execute(
            "INSERT OR IGNORE INTO heartrate (timestamp, bpm, day) VALUES (?, ?, ?)",
            (ts, bpm, day),
        )
        inserted += 1
    return inserted


def update_sync_log(conn: sqlite3.Connection, metric: str, last_day: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sync_log (metric, last_synced_day) VALUES (?, ?)",
        (metric, last_day),
    )


def get_last_synced_day(conn: sqlite3.Connection, metric: str) -> str | None:
    row = conn.execute(
        "SELECT last_synced_day FROM sync_log WHERE metric = ?", (metric,)
    ).fetchone()
    return row["last_synced_day"] if row else None


def get_daily_metrics(conn: sqlite3.Connection, metric: str, start: str, end: str) -> list[dict]:
    rows = conn.execute(
        """SELECT day, score, data_json FROM daily_metrics
           WHERE metric = ? AND day >= ? AND day <= ?
           ORDER BY day""",
        (metric, start, end),
    ).fetchall()
    result = []
    for row in rows:
        data = json.loads(row["data_json"])
        result.append({"day": row["day"], "score": row["score"], **data})
    return result


def get_heartrate(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    rows = conn.execute(
        "SELECT timestamp, bpm FROM heartrate WHERE day >= ? AND day <= ? ORDER BY timestamp",
        (start, end),
    ).fetchall()
    return [{"timestamp": row["timestamp"], "bpm": row["bpm"]} for row in rows]


def get_sync_status(conn: sqlite3.Connection) -> dict:
    metrics = [
        "sleep", "readiness", "activity", "stress", "spo2",
        "resilience", "cardiovascular_age", "vo2_max", "temperature", "heartrate",
    ]
    status = {}
    for metric in metrics:
        last_day = get_last_synced_day(conn, metric)
        if metric == "heartrate":
            count = conn.execute("SELECT COUNT(*) FROM heartrate").fetchone()[0]
        else:
            count = conn.execute(
                "SELECT COUNT(*) FROM daily_metrics WHERE metric = ?", (metric,)
            ).fetchone()[0]
        status[metric] = {"last_day": last_day, "rows": count}
    return status
