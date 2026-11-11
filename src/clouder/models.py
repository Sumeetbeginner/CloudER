import time
import uuid
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid() -> str:
    return str(uuid.uuid4())


def now() -> int:
    return int(time.time())


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(80))
    lock_version: Mapped[int] = mapped_column(Integer, default=0)
    storage_bytes: Mapped[int] = mapped_column(Integer, default=0)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[str] = mapped_column(String(20))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer, default=now)


class Resource(Base):
    __tablename__ = "resources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(63))
    state: Mapped[str] = mapped_column(String(20), default="pending")
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[int] = mapped_column(Integer, default=now)
    ready_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deleted_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    __table_args__ = (Index("ix_resource_tenant_kind", "tenant_id", "kind"),)


class Operation(Base):
    __tablename__ = "operations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    resource_id: Mapped[str] = mapped_column(ForeignKey("resources.id"))
    action: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[int] = mapped_column(Integer, default=now)
    lease_until: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=now)
    __table_args__ = (Index("ix_operation_claim", "state", "available_at", "lease_until"),)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[int] = mapped_column(Integer, default=now)
    seen_at: Mapped[int] = mapped_column(Integer, index=True)
    operation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)


class Idempotency(Base):
    __tablename__ = "idempotency"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    operation_id: Mapped[str] = mapped_column(ForeignKey("operations.id"))


class Audit(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    actor: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[int] = mapped_column(Integer, default=now)


class Secret(Base):
    __tablename__ = "secrets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[int] = mapped_column(Integer, default=now)
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)


class StoredObject(Base):
    __tablename__ = "objects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    bucket_id: Mapped[str] = mapped_column(ForeignKey("resources.id"))
    key: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer, default=now)
    __table_args__ = (UniqueConstraint("bucket_id", "key"),)
