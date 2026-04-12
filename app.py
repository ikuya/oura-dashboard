"""Flask web app: Oura Ring dashboard."""

import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

import db
import sync
from oura_client import OuraClient, OuraAPIError, _today_str, _n_days_ago_str

load_dotenv()

app = Flask(__name__, static_folder="static")
db.init_db()


def _get_client() -> OuraClient:
    token = os.environ.get("OURA_TOKEN")
    if not token:
        raise RuntimeError("OURA_TOKEN not set")
    return OuraClient(token)


def _parse_range() -> tuple[str, str]:
    end = request.args.get("end", _today_str())
    start = request.args.get("start", _n_days_ago_str(30))
    return start, end


# --- Static ---

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# --- API: metrics ---

DAILY_METRICS = [
    "sleep", "readiness", "activity", "stress", "spo2",
    "resilience", "cardiovascular_age", "vo2_max", "temperature",
]


@app.route("/api/metrics")
def get_metrics():
    start, end = _parse_range()
    requested = request.args.get("metric", ",".join(DAILY_METRICS))
    metrics = [m.strip() for m in requested.split(",") if m.strip()]

    with db.get_connection() as conn:
        result = {}
        for metric in metrics:
            if metric not in DAILY_METRICS:
                continue
            result[metric] = db.get_daily_metrics(conn, metric, start, end)

    return jsonify(result)


@app.route("/api/metrics/<metric>")
def get_metric(metric: str):
    if metric not in DAILY_METRICS:
        return jsonify({"error": f"Unknown metric: {metric}"}), 400
    start, end = _parse_range()
    with db.get_connection() as conn:
        records = db.get_daily_metrics(conn, metric, start, end)
    return jsonify(records)


# --- API: heartrate ---

@app.route("/api/heartrate")
def get_heartrate():
    start, end = _parse_range()
    with db.get_connection() as conn:
        records = db.get_heartrate(conn, start, end)
    return jsonify(records)


# --- API: sync ---

@app.route("/api/sync/status")
def sync_status():
    with db.get_connection() as conn:
        status = db.get_sync_status(conn)
    return jsonify(status)


@app.route("/api/sync", methods=["POST"])
def trigger_sync():
    body = request.get_json(silent=True) or {}
    requested_start = body.get("start")
    requested_end = body.get("end", _today_str())
    requested_metrics = body.get("metrics")

    try:
        client = _get_client()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    with db.get_connection() as conn:
        result = sync.run_sync(
            conn,
            client,
            requested_start=requested_start,
            requested_end=requested_end,
            metrics=requested_metrics,
        )

    return jsonify(result), 202


if __name__ == "__main__":
    app.run(debug=True)
