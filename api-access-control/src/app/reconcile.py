"""Reconciliation script (A18 in DESIGN.md):
Rebuilds or reconciles Redis monthly quota counters from the Postgres usage ledger.
After a Redis restart or flush, this can be run manually or as a periodic cron job.
"""
import os
import sys
from datetime import datetime, timezone
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
import redis

# Ensure project root is in sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

try:
    from .config import settings
    from .models import UsageEvent, Organization
    from .limiter import get_current_utc_month_info
except (ImportError, ValueError):
    from src.app.config import settings
    from src.app.models import UsageEvent, Organization
    from src.app.limiter import get_current_utc_month_info


def reconcile():
    postgres_url = (
        f"postgresql+psycopg2://{settings.POSTGRES_USER}:"
        f"{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:"
        f"{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    )
    engine = create_engine(postgres_url)
    Session = sessionmaker(bind=engine)
    session = Session()

    r = redis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        password=settings.REDIS_PASSWORD,
        decode_responses=True,
    )

    yyyymm, seconds_until_next, _ = get_current_utc_month_info()
    now = datetime.now(timezone.utc)
    month_start = datetime(now.year, now.month, 1, 0, 0, 0, tzinfo=timezone.utc)

    orgs = session.query(Organization).all()
    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting reconciliation for {len(orgs)} organizations (month: {yyyymm})...")

    drift_count = 0
    for org in orgs:
        # Sum all served usage events in Postgres for the current month
        actual_used = (
            session.query(func.coalesce(func.sum(UsageEvent.cost), 0))
            .filter(
                UsageEvent.org_id == org.id,
                UsageEvent.status == "served",
                UsageEvent.ts >= month_start,
            )
            .scalar()
        )

        quota_key = f"quota:{org.id}:{yyyymm}"
        cached_val = r.get(quota_key)
        cached_used = int(cached_val) if cached_val is not None else 0

        if cached_used != actual_used:
            print(f"Drift detected for Org {org.id} ({org.name}): Redis={cached_used}, Postgres={actual_used}. Syncing...")
            r.set(quota_key, actual_used)
            r.expire(quota_key, seconds_until_next + 86400)
            drift_count += 1
        else:
            print(f"Org {org.id} ({org.name}) in sync: {actual_used} units used.")

    session.close()
    print(f"Reconciliation complete. Corrected {drift_count} drifting organizations.")


if __name__ == "__main__":
    reconcile()
