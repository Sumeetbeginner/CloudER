import hashlib
import secrets
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from clouder.models import ApiKey, Audit, now

Role = Literal["owner", "operator", "viewer"]


@dataclass(frozen=True)
class Principal:
    key_id: str
    tenant_id: str
    role: str


def issue_key(
    session: Session, tenant_id: str, name: str, role: Role, days: int
) -> tuple[ApiKey, str]:
    token = "cer_" + secrets.token_urlsafe(32)
    key = ApiKey(
        tenant_id=tenant_id,
        name=name,
        role=role,
        digest=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=now() + days * 86400,
    )
    session.add(key)
    session.flush()
    return key, token


def authenticate(session: Session, token: str) -> Principal:
    digest = hashlib.sha256(token.encode()).hexdigest()
    key = session.scalar(
        select(ApiKey).where(
            ApiKey.digest == digest, ApiKey.revoked.is_(False), ApiKey.expires_at > now()
        )
    )
    if not key:
        raise HTTPException(
            401, "Invalid or expired API key", headers={"WWW-Authenticate": "Bearer"}
        )
    return Principal(key.id, key.tenant_id, key.role)


def require(principal: Principal, action: Literal["read", "write", "admin", "secret"]) -> None:
    allowed = {
        "owner": {"read", "write", "admin", "secret"},
        "operator": {"read", "write", "secret"},
        "viewer": {"read"},
    }
    if action not in allowed.get(principal.role, set()):
        raise HTTPException(403, "This role is not allowed to perform this action")


def audit(session: Session, principal: Principal, action: str, target: str) -> None:
    session.add(
        Audit(tenant_id=principal.tenant_id, actor=principal.key_id, action=action, target=target)
    )
