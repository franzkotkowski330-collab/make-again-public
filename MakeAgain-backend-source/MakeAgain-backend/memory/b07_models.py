"""B07 durable operations, commercial configuration and job queue."""
from sqlalchemy import String, Integer, DateTime, JSON, Text, UniqueConstraint, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from memory.db import Base
from memory.models import _uuid, utcnow
from datetime import datetime, timedelta

class Operation(Base):
    __tablename__ = 'operations'
    __table_args__ = (UniqueConstraint('user_id', 'request_id'),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    companion_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    endpoint: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), default='processing')
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    http_status: Mapped[int] = mapped_column(Integer, default=200)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=lambda: utcnow()+timedelta(minutes=10))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

class Audit(Base):
    __tablename__ = 'admin_audit'
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    actor_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(256))
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_safe: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

class ActivationBatch(Base):
    __tablename__ = 'activation_batches'
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128))
    duration_days: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

class ActivationCode(Base):
    __tablename__ = 'activation_codes'
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    batch_id: Mapped[str] = mapped_column(String(64), index=True)
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    suffix: Mapped[str] = mapped_column(String(8))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    used_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    entitlement_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class Plan(Base):
    __tablename__ = 'membership_plans'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    amount: Mapped[int] = mapped_column(Integer)
    duration_days: Mapped[int] = mapped_column(Integer)
    period_label: Mapped[str] = mapped_column(String(128))
    benefits: Mapped[str] = mapped_column(Text)
    terms: Mapped[str] = mapped_column(Text)
    platforms: Mapped[list] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)

class Order(Base):
    __tablename__ = 'payment_orders'
    __table_args__ = (UniqueConstraint('user_id', 'request_id'),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    plan_id: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSON)
    amount: Mapped[int] = mapped_column(Integer)
    duration_days: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default='processing')
    prepay_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class MessageReceipt(Base):
    __tablename__ = 'message_receipts'
    __table_args__ = (UniqueConstraint('user_id','request_id'),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    template_id: Mapped[str] = mapped_column(String(128))
    choice: Mapped[str] = mapped_column(String(16))
    remaining: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

class Job(Base):
    __tablename__ = 'durable_jobs'
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(32))
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    companion_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(256), unique=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default='pending')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

class CommunityBlock(Base):
    __tablename__='community_blocks'
    __table_args__=(UniqueConstraint('user_id','blocked_user_id'),)
    id:Mapped[str]=mapped_column(String(64),primary_key=True,default=_uuid)
    user_id:Mapped[str]=mapped_column(String(64),index=True)
    blocked_user_id:Mapped[str]=mapped_column(String(64))

class CommunityReport(Base):
    __tablename__='community_reports'
    id:Mapped[str]=mapped_column(String(64),primary_key=True,default=_uuid)
    user_id:Mapped[str]=mapped_column(String(64),index=True)
    letter_id:Mapped[str]=mapped_column(String(64))
    reply_id:Mapped[str|None]=mapped_column(String(64),nullable=True)
    reason:Mapped[str]=mapped_column(String(256))
    status:Mapped[str]=mapped_column(String(16),default='pending')
    created_at:Mapped[datetime]=mapped_column(DateTime,default=utcnow)
