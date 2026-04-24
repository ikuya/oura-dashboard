"""Shared fixtures for the test suite."""

import sqlite3

import pytest

import db


@pytest.fixture
def mem_conn():
    """In-memory SQLite connection with the full schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE daily_metrics (
            id        INTEGER PRIMARY KEY,
            metric    TEXT NOT NULL,
            day       TEXT NOT NULL,
            score     REAL,
            data_json TEXT NOT NULL,
            synced_at TEXT NOT NULL,
            UNIQUE(metric, day)
        );
        CREATE TABLE heartrate (
            id        INTEGER PRIMARY KEY,
            timestamp TEXT NOT NULL UNIQUE,
            bpm       INTEGER NOT NULL,
            day       TEXT NOT NULL
        );
        CREATE INDEX idx_heartrate_day ON heartrate(day);
        CREATE TABLE sync_log (
            metric          TEXT PRIMARY KEY,
            last_synced_day TEXT NOT NULL
        );
        CREATE TABLE advice_history (
            id           INTEGER PRIMARY KEY,
            saved_at     TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end   TEXT NOT NULL,
            content      TEXT NOT NULL
        );
        CREATE INDEX idx_advice_history_saved_at ON advice_history(saved_at);
    """)
    yield conn
    conn.close()
