"""Transactional domain operations, independent of HTTP and infrastructure providers."""

import hashlib
import json
import secrets
from typing import Any

from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from clouder.config import Settings
from clouder.models import Idempotency, Operation, Resource, Secret, StoredObject, Tenant
from clouder.schemas import CreateResource
from clouder.security import Principal, audit, require


def lock_tenant(session: Session, tenant_id: str) -> Tenant:
    # UPDATE obtains a write lock on SQLite and a row lock on PostgreSQL.
    # Every quota-changing operation takes this lock before reading usage.
    session.execute(
        update(Tenant).where(Tenant.id == tenant_id).values(lock_version=Tenant.lock_version + 1)
    )
    tenant = session.get(Tenant, tenant_id, populate_existing=True)
    if tenant is None:
        raise HTTPException(404, "Tenant not found")
    return tenant


def owned_resource(session: Session, principal: Principal, resource_id: str) -> Resource:
    resource = session.scalar(
        select(Resource).where(
            Resource.id == resource_id, Resource.tenant_id == principal.tenant_id
        )
    )
    if resource is None:
        raise HTTPException(404, "Resource not found")
    return resource


def fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def replay(session: Session, principal: Principal, key: str, digest: str) -> Operation | None:
    previous = session.get(Idempotency, (principal.tenant_id, key))
    if previous:
        if previous.fingerprint != digest:
            raise HTTPException(409, "Idempotency key was already used for a different request")
        return session.get(Operation, previous.operation_id)
    return None


def enqueue(
    session: Session, principal: Principal, resource: Resource, action: str, key: str, digest: str
) -> Operation:
    operation = Operation(tenant_id=principal.tenant_id, resource_id=resource.id, action=action)
    session.add(operation)
    session.flush()
    session.add(
        Idempotency(
            tenant_id=principal.tenant_id, key=key, fingerprint=digest, operation_id=operation.id
        )
    )
    audit(session, principal, f"{resource.kind}.{action}.requested", resource.id)
    return operation


def create_resource(
    session: Session, settings: Settings, principal: Principal, request: CreateResource, key: str
) -> Operation:
    require(principal, "write")
    lock_tenant(session, principal.tenant_id)
    digest = fingerprint({"action": "create", **request.model_dump()})
    previous = replay(session, principal, key, digest)
    if previous:
        return previous
    used = (
        session.scalar(
            select(func.count())
            .select_from(Resource)
            .where(Resource.tenant_id == principal.tenant_id, Resource.state != "deleted")
        )
        or 0
    )
    if used >= settings.resource_quota:
        raise HTTPException(409, "Resource quota exceeded; delete unused resources")
    duplicate = session.scalar(
        select(Resource.id).where(
            Resource.tenant_id == principal.tenant_id,
            Resource.kind == request.kind,
            Resource.name == request.name,
            Resource.state != "deleted",
        )
    )
    if duplicate:
        raise HTTPException(409, "An active resource already has this name")
    for target_id in request.spec.targets:
        target = owned_resource(session, principal, target_id)
        if target.kind != "compute" or target.state != "ready":
            raise HTTPException(409, "Load balancer targets must be ready compute resources")
    resource = Resource(
        tenant_id=principal.tenant_id,
        name=request.name,
        kind=request.kind,
        spec=request.spec.model_dump(),
    )
    session.add(resource)
    session.flush()
    if resource.kind == "database":
        # Store credentials before provisioning so a retry cannot change the password.
        session.add(
            Secret(
                tenant_id=principal.tenant_id,
                name=f"db-{resource.id}",
                ciphertext=Fernet(settings.encryption_key.encode()).encrypt(
                    secrets.token_urlsafe(32).encode()
                ),
            )
        )
    return enqueue(session, principal, resource, "create", key, digest)


def delete_resource(
    session: Session, principal: Principal, resource_id: str, key: str
) -> Operation:
    require(principal, "write")
    lock_tenant(session, principal.tenant_id)
    digest = fingerprint({"action": "delete", "id": resource_id})
    previous = replay(session, principal, key, digest)
    if previous:
        return previous
    resource = owned_resource(session, principal, resource_id)
    if resource.state not in {"ready", "failed"}:
        raise HTTPException(409, "Only ready or failed resources can be deleted")
    balancers = session.scalars(
        select(Resource).where(
            Resource.tenant_id == principal.tenant_id,
            Resource.kind == "loadbalancer",
            Resource.state != "deleted",
        )
    )
    if any(resource_id in item.spec["targets"] for item in balancers):
        raise HTTPException(409, "Delete dependent load balancers first")
    if resource.kind == "bucket" and session.scalar(
        select(StoredObject.id).where(StoredObject.bucket_id == resource.id).limit(1)
    ):
        raise HTTPException(409, "Bucket must be empty before deletion")
    resource.state = "deleting"
    return enqueue(session, principal, resource, "delete", key, digest)


def resource_view(resource: Resource) -> dict[str, Any]:
    return {
        "id": resource.id,
        "name": resource.name,
        "kind": resource.kind,
        "state": resource.state,
        "spec": resource.spec,
        "output": resource.output,
        "created_at": resource.created_at,
        "ready_at": resource.ready_at,
        "deleted_at": resource.deleted_at,
    }


def operation_view(operation: Operation) -> dict[str, Any]:
    return {
        "id": operation.id,
        "resource_id": operation.resource_id,
        "action": operation.action,
        "state": operation.state,
        "attempts": operation.attempts,
        "error": operation.error,
        "created_at": operation.created_at,
    }
