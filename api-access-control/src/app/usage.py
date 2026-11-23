import hmac
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import func

from .dependencies import get_db
from .config import settings
from .models import UsageEvent, Organization, Key, Plan
from .auth import hmac_hash

usage_router = APIRouter(tags=["Usage"])
bearer_scheme = HTTPBearer(auto_error=False)


@usage_router.get("/usage")
async def get_usage(
    from_date: Optional[datetime] = Query(None, alias="from"),
    to_date: Optional[datetime] = Query(None, alias="to"),
    granularity: str = Query("day", regex="^(hour|day)$"),
    org_id_query: Optional[int] = Query(None, alias="org_id"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
):
    """A15: Usage reporting endpoint.
    Accessible with a customer bearer key (restricted to that org)
    OR with X-Admin-Token (with optional org_id query).
    Does NOT consume quota.
    """
    target_org_id: Optional[int] = None

    # Check Admin Auth first
    if x_admin_token and hmac.compare_digest(x_admin_token, settings.ADMIN_TOKEN):
        if not org_id_query:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Admin must provide 'org_id' query parameter",
            )
        target_org_id = org_id_query
    elif credentials and credentials.scheme.lower() == "bearer":
        raw_key = credentials.credentials.strip()
        key_hash = hmac_hash(raw_key, settings.PEPPER)
        key_record = db.query(Key).filter(Key.secret_hash == key_hash, Key.revoked.is_(False)).first()
        if not key_record:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid customer API key",
            )
        target_org_id = key_record.org_id
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required (Bearer key or X-Admin-Token)",
        )

    org = db.query(Organization).filter(Organization.id == target_org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Current month UTC usage
    now = datetime.now(timezone.utc)
    first_of_month = datetime(now.year, now.month, 1, 0, 0, 0, tzinfo=timezone.utc)

    month_used = (
        db.query(func.coalesce(func.sum(UsageEvent.cost), 0))
        .filter(
            UsageEvent.org_id == target_org_id,
            UsageEvent.status == "served",
            UsageEvent.ts >= first_of_month,
        )
        .scalar()
    )

    allowance = org.plan.monthly_allowance
    remaining = max(0, allowance - month_used)

    # Time series query
    query = db.query(UsageEvent).filter(
        UsageEvent.org_id == target_org_id,
        UsageEvent.status == "served",
    )
    if from_date:
        query = query.filter(UsageEvent.ts >= from_date)
    if to_date:
        query = query.filter(UsageEvent.ts <= to_date)

    events = query.order_by(UsageEvent.ts.asc()).all()

    # Aggregate into buckets
    time_series_map: Dict[str, int] = {}
    date_format = "%Y-%m-%d %H:00" if granularity == "hour" else "%Y-%m-%d"

    for ev in events:
        bucket = ev.ts.strftime(date_format)
        time_series_map[bucket] = time_series_map.get(bucket, 0) + ev.cost

    time_series = [
        {"period": period, "count": count}
        for period, count in sorted(time_series_map.items())
    ]

    return {
        "org_id": org.id,
        "org_name": org.name,
        "plan": org.plan.name,
        "allowance": allowance,
        "used": month_used,
        "remaining": remaining,
        "granularity": granularity,
        "time_series": time_series,
    }
