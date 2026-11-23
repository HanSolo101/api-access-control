import os
import secrets
import hmac
import hashlib
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .config import settings
from .models import Base, Plan, Organization, Key


def hmac_hash(secret: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), secret.encode(), hashlib.sha256).hexdigest()


def generate_key_string() -> str:
    """A10: Format qk_<8-char id>_<43-char secret>."""
    key_id = secrets.token_hex(4)
    key_secret = secrets.token_urlsafe(32)[:43]
    return f"qk_{key_id}_{key_secret}"


def main():
    postgres_url = (
        f"postgresql+psycopg2://{settings.POSTGRES_USER}:"
        f"{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:"
        f"{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    )
    engine = create_engine(postgres_url)
    Session = sessionmaker(bind=engine)
    session = Session()

    Base.metadata.create_all(engine)

    # A9: Placeholder plans
    plans_data = [
        {"name": "free", "burst": 10, "refill_per_sec": 5.0, "monthly_allowance": 1000},
        {"name": "pro", "burst": 100, "refill_per_sec": 50.0, "monthly_allowance": 100000},
        {"name": "scale", "burst": 1000, "refill_per_sec": 500.0, "monthly_allowance": 5000000},
    ]

    seeded_plans = {}
    for p in plans_data:
        plan = session.query(Plan).filter_by(name=p["name"]).first()
        if not plan:
            plan = Plan(
                name=p["name"],
                burst=p["burst"],
                refill_per_sec=p["refill_per_sec"],
                monthly_allowance=p["monthly_allowance"],
            )
            session.add(plan)
            session.commit()
            print(f"Created plan '{p['name']}'.")
        seeded_plans[p["name"]] = plan

    # Seed demo organization
    org = session.query(Organization).filter_by(name="Acme Corp").first()
    if not org:
        org = Organization(name="Acme Corp", plan_id=seeded_plans["free"].id)
        session.add(org)
        session.commit()
        print("Created demo organization 'Acme Corp'.")

    # Seed demo key
    raw_key = generate_key_string()
    key_hash = hmac_hash(raw_key, settings.PEPPER)

    key = session.query(Key).filter_by(org_id=org.id, label="Primary Demo Key").first()
    if not key:
        key = Key(
            org_id=org.id,
            label="Primary Demo Key",
            secret_hash=key_hash,
        )
        session.add(key)
        session.commit()
        print("=" * 60)
        print("DEMO API KEY GENERATED (Save this, it is shown only once):")
        print(raw_key)
        print("=" * 60)

    session.close()


if __name__ == "__main__":
    main()
