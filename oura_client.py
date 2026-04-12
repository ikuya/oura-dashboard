"""Oura Ring API v2 client (copied from ../oura/oura.py)."""

import datetime

import requests

BASE_URL = "https://api.ouraring.com"
API_TIMEOUT = 15
DATE_FORMAT = "%Y-%m-%d"


class OuraAPIError(Exception):
    def __init__(self, status_code: int | None, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)


class OuraClient:
    def __init__(self, token: str) -> None:
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def _get(self, path: str, params: dict) -> list[dict]:
        url = BASE_URL + path
        try:
            response = self.session.get(url, params=params, timeout=API_TIMEOUT)
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            msg = f"HTTP {status}: {e.response.text if e.response is not None else str(e)}"
            if status == 401:
                msg += "\nHint: Check your OURA_TOKEN."
            raise OuraAPIError(status, msg) from e
        except requests.exceptions.RequestException as e:
            raise OuraAPIError(None, f"Request failed: {e}") from e
        return response.json().get("data", [])

    def get_daily_sleep(self, start: str, end: str) -> list[dict]:
        return self._get(
            "/v2/usercollection/daily_sleep",
            {"start_date": start, "end_date": end},
        )

    def get_daily_readiness(self, start: str, end: str) -> list[dict]:
        return self._get(
            "/v2/usercollection/daily_readiness",
            {"start_date": start, "end_date": end},
        )

    def get_heartrate(self, start: str, end: str) -> list[dict]:
        return self._get(
            "/v2/usercollection/heartrate",
            {
                "start_datetime": _date_to_datetime_str(start),
                "end_datetime": _date_to_datetime_str(end, end_of_day=True),
            },
        )

    def get_daily_activity(self, start: str, end: str) -> list[dict]:
        return self._get(
            "/v2/usercollection/daily_activity",
            {"start_date": start, "end_date": end},
        )

    def get_daily_stress(self, start: str, end: str) -> list[dict]:
        return self._get(
            "/v2/usercollection/daily_stress",
            {"start_date": start, "end_date": end},
        )

    def get_daily_spo2(self, start: str, end: str) -> list[dict]:
        return self._get(
            "/v2/usercollection/daily_spo2",
            {"start_date": start, "end_date": end},
        )

    def get_daily_resilience(self, start: str, end: str) -> list[dict]:
        return self._get(
            "/v2/usercollection/daily_resilience",
            {"start_date": start, "end_date": end},
        )

    def get_daily_cardiovascular_age(self, start: str, end: str) -> list[dict]:
        return self._get(
            "/v2/usercollection/daily_cardiovascular_age",
            {"start_date": start, "end_date": end},
        )

    def get_vo2_max(self, start: str, end: str) -> list[dict]:
        return self._get(
            "/v2/usercollection/vo2_max",
            {"start_date": start, "end_date": end},
        )


def _date_to_datetime_str(date_str: str, end_of_day: bool = False) -> str:
    time = "23:59:59" if end_of_day else "00:00:00"
    return f"{date_str}T{time}"


def _today_str() -> str:
    return datetime.date.today().strftime(DATE_FORMAT)


def _n_days_ago_str(n: int) -> str:
    return (datetime.date.today() - datetime.timedelta(days=n)).strftime(DATE_FORMAT)
