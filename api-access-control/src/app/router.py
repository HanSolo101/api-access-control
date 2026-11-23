import json
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
import redis.asyncio as redis

from .dependencies import get_db, get_redis
from .auth import get_key_and_org_info
from .models import UsageEvent
from .limiter import RateLimiter

router = APIRouter()


@router.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "OK"}


@router.post("/v1/quote")
async def quote(
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    key_info: dict = Depends(get_key_and_org_info),
    db: Session = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
):
    """Quotes endpoint with rate limiting, monthly quota, idempotency, and usage ledger."""
    org_id = key_info["org_id"]
    key_id = key_info["key_id"]
    burst = key_info["burst"]
    refill_per_sec = key_info["refill_per_sec"]
    monthly_allowance = key_info["monthly_allowance"]
    cost = 1

    # 1. Idempotency check (A17)
    idemp_cache_key = None
    if idempotency_key:
        idemp_cache_key = f"idemp:{org_id}:{idempotency_key}"
        try:
            cached_res = await r.get(idemp_cache_key)
            if cached_res:
                return JSONResponse(content=json.loads(cached_res), status_code=200)
        except redis.RedisError:
            # If Redis check fails, fail closed (A19)
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                headers={"Retry-After": "5"},
                content={"error": "service_unavailable", "detail": "Redis unavailable"},
            )

    # 2. Token bucket and monthly quota reservation (A2, A3, A6, A7, A16, A19)
    limiter = RateLimiter(r)
    try:
        check = await limiter.check_and_reserve(
            org_id=org_id,
            burst=burst,
            refill_per_sec=refill_per_sec,
            monthly_allowance=monthly_allowance,
            cost=cost,
        )
    except (redis.RedisError, ConnectionError):
        # A19: Redis down: fail closed. Return 503 with Retry-After.
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "5"},
            content={"error": "service_unavailable", "detail": "Rate limiting backend unavailable"},
        )

    if not check["allowed"]:
        # A7: Over-limit response: 429 with standard JSON & headers
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={
                "Retry-After": str(check["retry_after_seconds"]),
                "X-RateLimit-Limit": str(check["limit"]),
                "X-RateLimit-Remaining": str(check["remaining"]),
                "X-RateLimit-Reset": str(check["reset_at"]),
            },
            content={
                "error": check["reason"],
                "retry_after_seconds": check["retry_after_seconds"],
                "limit": check["limit"],
                "remaining": check["remaining"],
                "reset_at": check["reset_at"],
            },
        )

    # 3. Compute quote and record usage (A16)
    request_id = str(uuid.uuid4())
    try:
        usage = UsageEvent(
            request_id=request_id,
            org_id=org_id,
            key_id=key_id,
            cost=cost,
            status="served",
        )
        db.add(usage)
        db.commit()
    except Exception:
        db.rollback()
        # If DB insert fails, refund Redis reservation and return 503 (A16)
        try:
            await limiter.refund(org_id=org_id, burst=burst, cost=cost)
        except Exception:
            pass
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "5"},
            content={"error": "service_unavailable", "detail": "Failed to persist usage ledger"},
        )

    # 4. Build response payload
    payload = {
        "request_id": request_id,
        "quote": "The only way to do great work is to love what you do.",
        "author": "Steve Jobs",
        "org": key_info["org_name"],
        "plan": key_info["plan_name"],
    }

    # Store for idempotency if key was provided (24-hour expiration)
    if idemp_cache_key:
        try:
            await r.setex(idemp_cache_key, 86400, json.dumps(payload))
        except redis.RedisError:
            pass

    # Add standard rate limit headers to successful response
    headers = {
        "X-RateLimit-Limit": str(check["burst"]),
        "X-RateLimit-Remaining": str(check["remaining_tokens"]),
        "X-RateLimit-Reset": str(check["reset_at"]),
    }
    return JSONResponse(content=payload, status_code=200, headers=headers)
