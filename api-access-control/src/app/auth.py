import hmac
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
import redis.asyncio as redis

from .dependencies import get_db, get_redis
from .models import Key, Organization, Plan
from .config import settings

bearer_scheme = HTTPBearer(auto_error=False)


def constant_time_compare(val1: str, val2: str) -> bool:
    """Constant-time comparison."""
    return hmac.compare_digest(val1, val2)


def hmac_hash(secret: str, pepper: str) -> str:
    """Compute HMAC-SHA256(pepper, secret) as hex digest."""
    return hmac.new(pepper.encode(), secret.encode(), hashlib.sha256).hexdigest()


async def get_key_and_org_info(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
    db: Session = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
) -> Dict[str, Any]:
    """Authenticates the bearer token against Redis cache or Postgres database.
    Verifies key format, HMAC-SHA256 hash, revocation, and expiration.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )

    raw_key = credentials.credentials.strip()
    key_hash = hmac_hash(raw_key, settings.PEPPER)
    cache_key = f"keycache:{key_hash}"

    # Try fast Redis lookup first
    try:
        cached_data = await r.get(cache_key)
        if cached_data:
            data = json.loads(cached_data)
            _validate_key_status(data)
            return data
    except redis.RedisError:
        # If Redis fails, fall through to DB
        pass

    # DB lookup
    key_record = db.query(Key).filter(Key.secret_hash == key_hash).first()
    if not key_record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    org = db.query(Organization).filter(Organization.id == key_record.org_id).first()
    if not org or not org.plan:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Associated organization or plan not found",
        )

    expires_at_iso = key_record.expires_at.isoformat() if key_record.expires_at else None

    info = {
        "key_id": key_record.id,
        "org_id": org.id,
        "org_name": org.name,
        "plan_id": org.plan.id,
        "plan_name": org.plan.name,
        "burst": org.plan.burst,
        "refill_per_sec": float(org.plan.refill_per_sec),
        "monthly_allowance": org.plan.monthly_allowance,
        "revoked": key_record.revoked,
        "expires_at": expires_at_iso,
    }

    _validate_key_status(info)

    # Cache in Redis (TTL 5 minutes for resilience and quick invalidation)
    try:
        await r.setex(cache_key, 300, json.dumps(info))
    except redis.RedisError:
        pass

    return info


def _validate_key_status(data: Dict[str, Any]):
    """Checks revocation and expiration."""
    if data.get("revoked", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key has been revoked",
        )

    expires_at_str = data.get("expires_at")
    if expires_at_str:
        expires_at = datetime.fromisoformat(expires_at_str)
        if datetime.now(timezone.utc) > expires_at:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key has expired",
            )
