"""Daily sync script: run incremental sync and backfill the last 7 days of gaps."""

import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

import db
import sync
from oura_client import OuraClient

load_dotenv()

BACKFILL_DAYS = 7


def main() -> int:
    token = os.environ.get("OURA_TOKEN")
    if not token:
        print("ERROR: OURA_TOKEN not set", file=sys.stderr)
        return 1

    db.init_db()
    client = OuraClient(token)

    started_at = datetime.now(timezone.utc).isoformat()
    print(f"[{started_at}] Starting daily sync (backfill_days={BACKFILL_DAYS})")

    with db.get_connection() as conn:
        result = sync.run_sync(conn, client, backfill_days=BACKFILL_DAYS)

    for metric, count in result["synced"].items():
        print(f"  {metric}: {count} rows")
    if result["errors"]:
        print("Errors:")
        for metric, msg in result["errors"].items():
            print(f"  {metric}: {msg}", file=sys.stderr)

    finished_at = datetime.now(timezone.utc).isoformat()
    print(f"[{finished_at}] Done")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
