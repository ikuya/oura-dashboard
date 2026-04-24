"""Tests for Flask API endpoints in app.py."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

import db


@pytest.fixture
def app_client(mem_conn, tmp_path, monkeypatch):
    """Flask test client with in-memory DB and env vars set."""
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("APP_PASSWORD", "test-password")
    monkeypatch.setenv("OURA_TOKEN", "test-token")

    # Redirect DB to in-memory connection
    monkeypatch.setattr(db, "get_connection", lambda: mem_conn)
    monkeypatch.setattr(db, "init_db", lambda: None)

    import importlib
    import app as app_module
    importlib.reload(app_module)
    monkeypatch.setattr(db, "get_connection", lambda: mem_conn)

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield client


@pytest.fixture
def authed_client(app_client):
    """Flask test client already authenticated."""
    app_client.post(
        "/api/login",
        data=json.dumps({"password": "test-password"}),
        content_type="application/json",
    )
    return app_client


# --- Auth ---

def test_login_success(app_client):
    resp = app_client.post(
        "/api/login",
        data=json.dumps({"password": "test-password"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_login_wrong_password(app_client):
    resp = app_client.post(
        "/api/login",
        data=json.dumps({"password": "wrong"}),
        content_type="application/json",
    )
    assert resp.status_code == 401


def test_logout(authed_client):
    resp = authed_client.post("/api/logout")
    assert resp.status_code == 200
    # After logout, protected endpoint should return 401
    resp2 = authed_client.get("/api/metrics")
    assert resp2.status_code == 401


def test_protected_endpoint_requires_auth(app_client):
    resp = app_client.get("/api/metrics")
    assert resp.status_code == 401


# --- GET /api/metrics ---

def test_get_metrics_empty(authed_client, mem_conn):
    resp = authed_client.get("/api/metrics?start=2024-01-01&end=2024-01-31")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, dict)


def test_get_metrics_with_data(authed_client, mem_conn):
    db.upsert_daily_metric(mem_conn, "sleep", "2024-01-10", 80, {"score": 80})
    mem_conn.commit()

    resp = authed_client.get("/api/metrics?metric=sleep&start=2024-01-01&end=2024-01-31")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["sleep"]) == 1
    assert data["sleep"][0]["score"] == 80


def test_get_metrics_ignores_unknown_metric(authed_client, mem_conn):
    resp = authed_client.get("/api/metrics?metric=sleep,unknown_metric")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "unknown_metric" not in data
    assert "sleep" in data


# --- GET /api/metrics/<metric> ---

def test_get_metric_valid(authed_client, mem_conn):
    db.upsert_daily_metric(mem_conn, "readiness", "2024-01-05", 75, {"score": 75})
    mem_conn.commit()

    resp = authed_client.get("/api/metrics/readiness?start=2024-01-01&end=2024-01-31")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data[0]["score"] == 75


def test_get_metric_unknown_returns_400(authed_client):
    resp = authed_client.get("/api/metrics/unknown")
    assert resp.status_code == 400


# --- GET /api/heartrate ---

def test_get_heartrate(authed_client, mem_conn):
    records = [{"timestamp": "2024-01-10T12:00:00", "bpm": 65}]
    db.upsert_heartrate_batch(mem_conn, records)
    mem_conn.commit()

    resp = authed_client.get("/api/heartrate?start=2024-01-01&end=2024-01-31")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 1
    assert data[0]["bpm"] == 65


# --- GET /api/sync/status ---

def test_sync_status(authed_client, mem_conn):
    resp = authed_client.get("/api/sync/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "sleep" in data
    assert "heartrate" in data


# --- POST /api/sync ---

def test_trigger_sync_success(authed_client, mem_conn):
    mock_result = {"synced": {"sleep": 5}, "errors": {}}
    with patch("sync.run_sync", return_value=mock_result):
        resp = authed_client.post(
            "/api/sync",
            data=json.dumps({"start": "2024-01-01", "end": "2024-01-31"}),
            content_type="application/json",
        )
    assert resp.status_code == 202
    assert resp.get_json()["synced"]["sleep"] == 5


def test_trigger_sync_no_token(authed_client, monkeypatch):
    monkeypatch.delenv("OURA_TOKEN", raising=False)
    resp = authed_client.post("/api/sync", content_type="application/json")
    assert resp.status_code == 500


# --- POST /api/advice ---

def test_get_advice_no_data(authed_client, mem_conn):
    with patch("app._build_health_payload", return_value={
        "period": {"start": "2024-01-01", "end": "2024-01-14", "days": 14},
        "metrics": {m: [] for m in ["sleep", "readiness", "activity", "stress",
                                     "spo2", "resilience", "cardiovascular_age",
                                     "vo2_max", "temperature"]},
    }):
        resp = authed_client.post("/api/advice")
    assert resp.status_code == 400


def test_get_advice_success(authed_client, mem_conn):
    health_payload = {
        "period": {"start": "2024-01-01", "end": "2024-01-14", "days": 14},
        "metrics": {"sleep": [{"day": "2024-01-14", "score": 80}],
                    **{m: [] for m in ["readiness", "activity", "stress",
                                        "spo2", "resilience", "cardiovascular_age",
                                        "vo2_max", "temperature"]}},
    }
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "健康状態は良好です。"

    with patch("app._build_health_payload", return_value=health_payload), \
         patch("subprocess.run", return_value=mock_proc):
        resp = authed_client.post("/api/advice")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["advice"] == "健康状態は良好です。"
    assert data["period"]["start"] == "2024-01-01"


def test_get_advice_claude_not_found(authed_client):
    import subprocess
    health_payload = {
        "period": {"start": "2024-01-01", "end": "2024-01-14", "days": 14},
        "metrics": {"sleep": [{"day": "2024-01-14", "score": 80}],
                    **{m: [] for m in ["readiness", "activity", "stress",
                                        "spo2", "resilience", "cardiovascular_age",
                                        "vo2_max", "temperature"]}},
    }
    with patch("app._build_health_payload", return_value=health_payload), \
         patch("subprocess.run", side_effect=FileNotFoundError):
        resp = authed_client.post("/api/advice")

    assert resp.status_code == 500


def test_get_advice_claude_timeout(authed_client):
    import subprocess
    health_payload = {
        "period": {"start": "2024-01-01", "end": "2024-01-14", "days": 14},
        "metrics": {"sleep": [{"day": "2024-01-14", "score": 80}],
                    **{m: [] for m in ["readiness", "activity", "stress",
                                        "spo2", "resilience", "cardiovascular_age",
                                        "vo2_max", "temperature"]}},
    }
    with patch("app._build_health_payload", return_value=health_payload), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 120)):
        resp = authed_client.post("/api/advice")

    assert resp.status_code == 504


# --- GET /api/advice/history ---

def test_get_advice_history(authed_client, mem_conn):
    with db.transaction(mem_conn):
        db.save_advice(mem_conn, "2024-01-01", "2024-01-14", "some advice")

    resp = authed_client.get("/api/advice/history")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 1


# --- GET /api/advice/history/<date> ---

def test_get_advice_entry_valid(authed_client, mem_conn):
    with db.transaction(mem_conn):
        mem_conn.execute(
            "INSERT INTO advice_history (saved_at, period_start, period_end, content) VALUES (?, ?, ?, ?)",
            ("2024-01-14T10:00:00+00:00", "2024-01-01", "2024-01-14", "テストアドバイス"),
        )

    resp = authed_client.get("/api/advice/history/2024-01-14")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["advice"] == "テストアドバイス"


def test_get_advice_entry_invalid_date_format(authed_client):
    resp = authed_client.get("/api/advice/history/not-a-date")
    assert resp.status_code == 400


def test_get_advice_entry_not_found(authed_client):
    resp = authed_client.get("/api/advice/history/2024-01-01")
    assert resp.status_code == 404
