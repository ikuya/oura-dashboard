"""Flask web app: Oura Ring dashboard."""

import json as _json
import os
import subprocess
from datetime import date, timedelta

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
    payload = {}
    for metric in DAILY_METRICS:
        rows = db.get_daily_metrics(conn, metric, start, today)
        payload[metric] = [_extract_key_fields(metric, r) for r in rows]
    return {"period": {"start": start, "end": today, "days": days}, "metrics": payload}


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


# --- API: advice ---

@app.route("/api/advice", methods=["POST"])
def get_advice():
    with db.get_connection() as conn:
        health_data = _build_health_payload(conn, days=14)

    if not any(health_data["metrics"].values()):
        return jsonify({"error": "データがありません。まずSyncを実行してください。"}), 400

    prompt = (
        ADVICE_SYSTEM_PROMPT
        + "\n\n以下は私の直近14日間のOura Ringデータです。分析とアドバイスをお願いします。\n\n"
        + "```json\n"
        + _json.dumps(health_data, ensure_ascii=False, indent=2)
        + "\n```"
    )

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--max-turns", "1"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        return jsonify({"error": "claude コマンドが見つかりません。Claude Code がインストールされているか確認してください。"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "分析がタイムアウトしました。"}), 504

    if result.returncode != 0:
        return jsonify({"error": result.stderr or "Claude Code の実行に失敗しました。"}), 502

    return jsonify({
        "advice": result.stdout,
        "period": health_data["period"],
    })


if __name__ == "__main__":
    app.run(debug=True)
