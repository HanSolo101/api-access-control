import hmac
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import redis.asyncio as redis

from .dependencies import get_db, get_redis
from .config import settings
from .models import Organization, Plan, Key
from .auth import hmac_hash

admin_router = APIRouter(prefix="/admin", tags=["Admin"])


def verify_admin_token(x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token")):
    """Constant-time validation of the X-Admin-Token header (A13)."""
    if not x_admin_token or not hmac.compare_digest(x_admin_token, settings.ADMIN_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Admin-Token",
        )


class CreateOrgRequest(BaseModel):
    name: str = Field(..., min_length=1, examples=["My Organization"])
    plan_id: int = Field(..., ge=1, examples=[1])


class CreateKeyRequest(BaseModel):
    org_id: int = Field(..., ge=1, examples=[1])
    label: str = Field(..., min_length=1, examples=["Production Key"])


class RotateKeyRequest(BaseModel):
    grace_seconds: int = Field(86400, ge=0, le=604800)  # default 24h, 0 to 7 days (A12)


class ChangePlanRequest(BaseModel):
    plan_id: int = Field(..., ge=1, examples=[2])


def generate_key_string() -> str:
    """A10: Key format qk_<8-char id>_<43-char secret>."""
    key_id = secrets.token_hex(4)  # 8 hex chars
    key_secret = secrets.token_urlsafe(32)[:43]  # 43 chars
    return f"qk_{key_id}_{key_secret}"


@admin_router.get("/plans", dependencies=[Depends(verify_admin_token)])
async def list_plans(db: Session = Depends(get_db)):
    """List all available plans and their limits."""
    plans = db.query(Plan).order_by(Plan.id.asc()).all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "burst": p.burst,
            "refill_per_sec": float(p.refill_per_sec),
            "monthly_allowance": p.monthly_allowance,
        }
        for p in plans
    ]


@admin_router.get("/orgs", dependencies=[Depends(verify_admin_token)])
async def list_organizations(db: Session = Depends(get_db)):
    """List all organizations and their assigned plans."""
    orgs = db.query(Organization).order_by(Organization.id.asc()).all()
    return [
        {
            "id": o.id,
            "name": o.name,
            "plan_id": o.plan_id,
            "plan_name": o.plan.name if o.plan else None,
        }
        for o in orgs
    ]


@admin_router.post("/orgs", dependencies=[Depends(verify_admin_token)])
async def create_organization(payload: CreateOrgRequest, db: Session = Depends(get_db)):
    """A14: Create a new organization."""
    plan = db.query(Plan).filter(Plan.id == payload.plan_id).first()
    if not plan:
        raise HTTPException(status_code=400, detail="Plan does not exist")

    existing = db.query(Organization).filter(Organization.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Organization name already taken")

    org = Organization(name=payload.name, plan_id=payload.plan_id)
    db.add(org)
    db.commit()
    db.refresh(org)
    return {"id": org.id, "name": org.name, "plan_id": org.plan_id}


@admin_router.post("/keys", dependencies=[Depends(verify_admin_token)])
async def create_key(payload: CreateKeyRequest, db: Session = Depends(get_db)):
    """A10, A14: Generate a new API key. Secret shown ONCE."""
    org = db.query(Organization).filter(Organization.id == payload.org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    raw_key = generate_key_string()
    key_hash = hmac_hash(raw_key, settings.PEPPER)

    key_record = Key(
        org_id=org.id,
        label=payload.label,
        secret_hash=key_hash,
    )
    db.add(key_record)
    db.commit()
    db.refresh(key_record)

    return {
        "id": key_record.id,
        "org_id": key_record.org_id,
        "label": key_record.label,
        "key": raw_key,  # Full key returned ONCE
        "created_at": key_record.created_at.isoformat() if key_record.created_at else None,
    }


@admin_router.post("/keys/{key_id}/rotate", dependencies=[Depends(verify_admin_token)])
async def rotate_key(
    key_id: int,
    payload: RotateKeyRequest = RotateKeyRequest(),
    db: Session = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
):
    """A12: Rotate key: create new key for same org, set old key expires_at."""
    old_key = db.query(Key).filter(Key.id == key_id).first()
    if not old_key:
        raise HTTPException(status_code=404, detail="Key not found")

    # Set expiration on old key
    now = datetime.now(timezone.utc)
    old_key.expires_at = now + timedelta(seconds=payload.grace_seconds)

    # Invalidate cache of old key
    try:
        await r.delete(f"keycache:{old_key.secret_hash}")
    except redis.RedisError:
        pass

    # Create new key
    raw_key = generate_key_string()
    new_hash = hmac_hash(raw_key, settings.PEPPER)
    new_key = Key(
        org_id=old_key.org_id,
        label=f"{old_key.label} (rotated)",
        secret_hash=new_hash,
    )
    db.add(new_key)
    db.commit()
    db.refresh(new_key)

    return {
        "old_key_id": old_key.id,
        "old_key_expires_at": old_key.expires_at.isoformat(),
        "new_key_id": new_key.id,
        "new_key": raw_key,  # New raw key returned ONCE
    }


@admin_router.post("/keys/{key_id}/revoke", dependencies=[Depends(verify_admin_token)])
async def revoke_key(
    key_id: int,
    db: Session = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
):
    """A12: Immediate revocation: mark revoked and evict Redis cache."""
    key = db.query(Key).filter(Key.id == key_id).first()
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")

    key.revoked = True
    db.commit()

    # Invalidate Redis cache immediately
    try:
        await r.delete(f"keycache:{key.secret_hash}")
    except redis.RedisError:
        pass

    return {"id": key.id, "revoked": True}


@admin_router.patch("/orgs/{org_id}/plan", dependencies=[Depends(verify_admin_token)])
async def change_plan(
    org_id: int,
    payload: ChangePlanRequest,
    db: Session = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
):
    """A8: Plan changes apply immediately. Invalidate key cache for that org."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    plan = db.query(Plan).filter(Plan.id == payload.plan_id).first()
    if not plan:
        raise HTTPException(status_code=400, detail="Plan does not exist")

    org.plan_id = plan.id
    db.commit()

    # Invalidate key cache entries for all keys belonging to this org
    keys = db.query(Key).filter(Key.org_id == org_id).all()
    try:
        for k in keys:
            await r.delete(f"keycache:{k.secret_hash}")
    except redis.RedisError:
        pass

    return {
        "org_id": org.id,
        "plan_id": plan.id,
        "plan_name": plan.name,
        "burst": plan.burst,
        "refill_per_sec": float(plan.refill_per_sec),
        "monthly_allowance": plan.monthly_allowance,
    }
