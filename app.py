"""Flask web app: Oura Ring dashboard."""

import json as _json
import os
import subprocess
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory, session

import db
import sync
from oura_client import OuraClient, OuraAPIError, _today_str, _n_days_ago_str

load_dotenv()

app = Flask(__name__, static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("SECRET_KEY environment variable is not set")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

db.init_db()


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def _get_client() -> OuraClient:
    token = os.environ.get("OURA_TOKEN")
    if not token:
        raise RuntimeError("OURA_TOKEN not set")
    return OuraClient(token)


def _parse_range() -> tuple[str, str]:
    end = request.args.get("end", _today_str())
    start = request.args.get("start", _n_days_ago_str(30))
    return start, end


# --- Auth ---

@app.route("/api/login", methods=["POST"])
def login():
    password = os.environ.get("APP_PASSWORD")
    if not password:
        return jsonify({"error": "APP_PASSWORD not configured"}), 500
    body = request.get_json(silent=True) or {}
    if body.get("password") == password:
        session.permanent = True
        session["authenticated"] = True
        return jsonify({"ok": True})
    return jsonify({"error": "Invalid password"}), 401


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


# --- Static ---

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# --- API: metrics ---

DAILY_METRICS = [
    "sleep", "readiness", "activity", "stress", "spo2",
    "resilience", "cardiovascular_age", "vo2_max", "temperature",
]

_advice_jobs_lock = threading.Lock()
_advice_jobs: dict[str, dict] = {}

ADVICE_SYSTEM_PROMPT = """\
あなたはOura Ringの健康データを解析する専門家アシスタントです。
ユーザーから過去14日間のOura Ringデータ（睡眠、準備度、活動量、ストレス、血中酸素濃度、体温偏差、回復力、VO2 Max、心血管年齢）が提供されます。

## 役割
1. データを客観的に分析し、現在の健康状態を簡潔に要約する
2. トレンドや注目すべき変化点を特定する
3. 実践的かつ具体的なアドバイスを提供する

## 出力フォーマット
以下の構成で回答してください：

### 📊 現在の健康状態
（各メトリクスの直近の数値とトレンドを2〜3文でまとめる）

### ⚠️ 注目ポイント
（気になる変化・改善が必要な項目を箇条書きで列挙。良い場合は「特になし」と記載）

### 💡 アドバイス
（データに基づいた具体的な行動提案を3〜5項目の箇条書きで記載）

---
- すべての回答は日本語で行うこと
- スコアの良し悪しの判断基準：80以上=良好（緑）、60〜79=普通（黄）、60未満=要注意（赤）
- 体温偏差は±0.5°C以内が正常範囲
- 医療的な診断は行わないこと
"""


def _extract_key_fields(metric: str, row: dict) -> dict:
    base = {"day": row.get("day"), "score": row.get("score")}
    if metric in ("sleep", "readiness"):
        base["contributors"] = row.get("contributors")
    elif metric == "activity":
        base["active_calories"] = row.get("active_calories")
        base["steps"] = row.get("steps")
    elif metric == "stress":
        base["stress_high"] = row.get("stress_high")
        base["recovery_high"] = row.get("recovery_high")
    elif metric == "spo2":
        base["spo2_percentage"] = row.get("spo2_percentage")
    elif metric == "temperature":
        base["temperature_deviation"] = row.get("temperature_deviation")
        base["temperature_trend_deviation"] = row.get("temperature_trend_deviation")
    elif metric == "resilience":
        base["level"] = row.get("level")
    elif metric == "vo2_max":
        base["vo2_max"] = row.get("vo2_max")
    elif metric == "cardiovascular_age":
        base["vascular_age"] = row.get("vascular_age")
    return base


def _build_health_payload(conn, days: int = 14) -> dict:
    today = _today_str()
    start = (date.fromisoformat(today) - timedelta(days=days - 1)).isoformat()
    metrics_data = db.get_daily_metrics_bulk(conn, DAILY_METRICS, start, today)
    payload = {
        metric: [_extract_key_fields(metric, r) for r in metrics_data.get(metric, [])]
        for metric in DAILY_METRICS
    }
    return {"period": {"start": start, "end": today, "days": days}, "metrics": payload}


def _build_advice_prompt(health_data: dict) -> str:
    return (
        ADVICE_SYSTEM_PROMPT
        + "\n\n以下は私の直近14日間のOura Ringデータです。分析とアドバイスをお願いします。\n\n"
        + "```json\n"
        + _json.dumps(health_data, ensure_ascii=False, indent=2)
        + "\n```"
    )


def _set_advice_job(job_id: str, **updates) -> dict | None:
    with _advice_jobs_lock:
        job = _advice_jobs.get(job_id)
        if job is None:
            return None
        job.update(updates)
        return dict(job)


def _create_advice_job(period: dict) -> str:
    job_id = str(uuid.uuid4())
    with _advice_jobs_lock:
        _advice_jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "period": period,
            "advice": "",
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    return job_id


def _run_advice_job(job_id: str, prompt: str) -> None:
    _set_advice_job(
        job_id,
        status="running",
        updated_at=datetime.now(timezone.utc).isoformat(),
    )

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--max-turns", "1"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        _set_advice_job(
            job_id,
            status="failed",
            error="claude コマンドが見つかりません。Claude Code がインストールされているか確認してください。",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        return
    except subprocess.TimeoutExpired:
        _set_advice_job(
            job_id,
            status="failed",
            error="分析がタイムアウトしました。",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        return

    if result.returncode != 0:
        _set_advice_job(
            job_id,
            status="failed",
            error=result.stderr or "Claude Code の実行に失敗しました。",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        return

    saved_advice = result.stdout
    job = _set_advice_job(
        job_id,
        status="completed",
        advice=saved_advice,
        error=None,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )

    if not job:
        return

    period = job["period"]
    try:
        with db.get_connection() as conn:
            with db.transaction(conn):
                db.save_advice(conn, period["start"], period["end"], saved_advice)
    except Exception as e:
        app.logger.error("Failed to save advice: %s", e)


@app.route("/api/metrics")
@login_required
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
@login_required
def get_metric(metric: str):
    if metric not in DAILY_METRICS:
        return jsonify({"error": f"Unknown metric: {metric}"}), 400
    start, end = _parse_range()
    with db.get_connection() as conn:
        records = db.get_daily_metrics(conn, metric, start, end)
    return jsonify(records)


# --- API: heartrate ---

@app.route("/api/heartrate")
@login_required
def get_heartrate():
    start, end = _parse_range()
    with db.get_connection() as conn:
        records = db.get_heartrate(conn, start, end)
    return jsonify(records)


# --- API: sync ---

@app.route("/api/sync/status")
@login_required
def sync_status():
    with db.get_connection() as conn:
        status = db.get_sync_status(conn)
    return jsonify(status)


@app.route("/api/sync", methods=["POST"])
@login_required
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


# --- API: advice ---

@app.route("/api/advice", methods=["POST"])
@login_required
def get_advice():
    with db.get_connection() as conn:
        health_data = _build_health_payload(conn, days=14)

    if not any(health_data["metrics"].values()):
        return jsonify({"error": "データがありません。まずSyncを実行してください。"}), 400

    prompt = _build_advice_prompt(health_data)
    job_id = _create_advice_job(health_data["period"])

    worker = threading.Thread(target=_run_advice_job, args=(job_id, prompt), daemon=True)
    worker.start()

    return jsonify({"job_id": job_id, "status": "queued"}), 202


@app.route("/api/advice/<job_id>", methods=["GET"])
@login_required
def get_advice_job(job_id: str):
    with _advice_jobs_lock:
        job = _advice_jobs.get(job_id)

    if job is None:
        return jsonify({"error": "Job not found"}), 404

    base = {
        "job_id": job["id"],
        "status": job["status"],
        "period": job["period"],
    }
    if job["status"] == "completed":
        return jsonify({**base, "advice": job["advice"]})
    if job["status"] == "failed":
        return jsonify({**base, "error": job["error"]}), 502
    return jsonify(base), 202


@app.route("/api/advice/history")
@login_required
def get_advice_history():
    with db.get_connection() as conn:
        dates = db.get_advice_dates(conn)
    return jsonify(dates)


@app.route("/api/advice/history/<date>")
@login_required
def get_advice_entry(date: str):
    import re
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return jsonify({"error": "Invalid date format"}), 400
    with db.get_connection() as conn:
        entry = db.get_advice_for_date(conn, date)
    if entry is None:
        return jsonify({"error": "No advice for this date"}), 404
    return jsonify({
        "advice": entry["content"],
        "period": {"start": entry["period_start"], "end": entry["period_end"]},
        "saved_at": entry["saved_at"],
    })


if __name__ == "__main__":
    app.run(debug=True)
