from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Numeric, Text, func
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class Organization(Base):
    __tablename__ = "organizations"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    plan_id = Column(Integer, ForeignKey("plans.id"), nullable=False)
    plan = relationship("Plan", back_populates="organizations")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Plan(Base):
    __tablename__ = "plans"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    burst = Column(Integer, nullable=False)                     # max burst tokens
    refill_per_sec = Column(Numeric(10, 4), nullable=False)     # tokens per second
    monthly_allowance = Column(Integer, nullable=False)        # total units per month
    organizations = relationship("Organization", back_populates="plan")

class Key(Base):
    __tablename__ = "keys"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    label = Column(String, nullable=False)
    secret_hash = Column(String, nullable=False)   # HMAC-SHA256(secret, pepper)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    organization = relationship("Organization")

class UsageEvent(Base):
    __tablename__ = "usage_events"
    request_id = Column(String, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    key_id = Column(Integer, ForeignKey("keys.id"), nullable=False)
    ts = Column(DateTime(timezone=True), server_default=func.now())
    cost = Column(Integer, nullable=False, default=1)
    status = Column(String, nullable=False)  # served, rejected, error, etc.
    # optional extra data
    details = Column(Text, nullable=True)
    organization = relationship("Organization")
    key = relationship("Key")
