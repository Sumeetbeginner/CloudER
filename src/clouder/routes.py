from typing import Annotated, Any

from cryptography.fernet import Fernet
from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import func, select

from clouder.dependencies import KeyDep, PrincipalDep, SessionDep, SettingsDep
from clouder.models import ApiKey, Audit, Operation, Resource, Secret, StoredObject, Tenant, now
from clouder.schemas import CreateResource, KeyCreate, Kind, SecretWrite
from clouder.security import audit, issue_key, require
from clouder.services import (
    create_resource,
    delete_resource,
    lock_tenant,
    operation_view,
    owned_resource,
    resource_view,
)

router = APIRouter(prefix="/v1")
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100000)]


@router.get("/me", tags=["identity"])
def me(session: SessionDep, principal: PrincipalDep, settings: SettingsDep) -> dict[str, Any]:
    tenant = session.get(Tenant, principal.tenant_id)
    assert tenant
    return {
        "tenant_id": tenant.id,
        "tenant_name": tenant.name,
        "role": principal.role,
        "key_id": principal.key_id,
        "provider": settings.provider,
        "quotas": {
            "resources": settings.resource_quota,
            "storage_bytes": settings.storage_quota_bytes,
        },
        "storage_bytes": tenant.storage_bytes,
    }


@router.get("/resources", tags=["resources"])
def list_resources(
    session: SessionDep,
    principal: PrincipalDep,
    limit: Limit = 50,
    offset: Offset = 0,
    kind: Kind | None = None,
) -> dict[str, Any]:
    query = select(Resource).where(Resource.tenant_id == principal.tenant_id)
    if kind:
        query = query.where(Resource.kind == kind)
    query = query.order_by(Resource.created_at.desc(), Resource.id).offset(offset).limit(limit)
    return {
        "items": [resource_view(item) for item in session.scalars(query)],
        "limit": limit,
        "offset": offset,
    }


@router.get("/summary", tags=["monitoring"])
def summary(session: SessionDep, principal: PrincipalDep) -> dict[str, Any]:
    counts = session.execute(
        select(Resource.kind, func.count())
        .where(Resource.tenant_id == principal.tenant_id, Resource.state != "deleted")
        .group_by(Resource.kind)
    )
    by_kind = dict(counts.all())
    ready_count = (
        session.scalar(
            select(func.count())
            .select_from(Resource)
            .where(Resource.tenant_id == principal.tenant_id, Resource.state == "ready")
        )
        or 0
    )
    object_count = (
        session.scalar(
            select(func.count())
            .select_from(StoredObject)
            .where(StoredObject.tenant_id == principal.tenant_id)
        )
        or 0
    )
    tenant = session.get(Tenant, principal.tenant_id)
    assert tenant
    return {
        "active_resources": sum(by_kind.values()),
        "ready_resources": ready_count,
        "by_kind": by_kind,
        "objects": object_count,
        "storage_bytes": tenant.storage_bytes,
    }


@router.get("/resources/{resource_id}", tags=["resources"])
def get_resource(resource_id: str, session: SessionDep, principal: PrincipalDep) -> dict[str, Any]:
    return resource_view(owned_resource(session, principal, resource_id))


@router.post("/resources", status_code=202, tags=["resources"])
def provision(
    body: CreateResource,
    response: Response,
    session: SessionDep,
    principal: PrincipalDep,
    settings: SettingsDep,
    key: KeyDep,
) -> dict[str, Any]:
    operation = create_resource(session, settings, principal, body, key)
    response.headers["Location"] = f"/v1/operations/{operation.id}"
    return operation_view(operation)


@router.delete("/resources/{resource_id}", status_code=202, tags=["resources"])
def remove(
    resource_id: str, response: Response, session: SessionDep, principal: PrincipalDep, key: KeyDep
) -> dict[str, Any]:
    operation = delete_resource(session, principal, resource_id, key)
    response.headers["Location"] = f"/v1/operations/{operation.id}"
    return operation_view(operation)


@router.get("/operations", tags=["operations"])
def operations(
    session: SessionDep, principal: PrincipalDep, limit: Limit = 50, offset: Offset = 0
) -> dict[str, Any]:
    items = session.scalars(
        select(Operation)
        .where(Operation.tenant_id == principal.tenant_id)
        .order_by(Operation.created_at.desc(), Operation.id)
        .offset(offset)
        .limit(limit)
    )
    return {"items": [operation_view(item) for item in items], "limit": limit, "offset": offset}


@router.get("/operations/{operation_id}", tags=["operations"])
def operation(operation_id: str, session: SessionDep, principal: PrincipalDep) -> dict[str, Any]:
    item = session.scalar(
        select(Operation).where(
            Operation.id == operation_id, Operation.tenant_id == principal.tenant_id
        )
    )
    if not item:
        raise HTTPException(404, "Operation not found")
    return operation_view(item)


@router.get("/audit", tags=["audit"])
def audit_events(
    session: SessionDep, principal: PrincipalDep, limit: Limit = 50, offset: Offset = 0
) -> dict[str, Any]:
    rows = session.scalars(
        select(Audit)
        .where(Audit.tenant_id == principal.tenant_id)
        .order_by(Audit.created_at.desc(), Audit.id)
        .offset(offset)
        .limit(limit)
    )
    return {
        "items": [
            {
                "id": item.id,
                "actor": item.actor,
                "action": item.action,
                "target": item.target,
                "created_at": item.created_at,
            }
            for item in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@router.get("/keys", tags=["identity"])
def keys(session: SessionDep, principal: PrincipalDep) -> dict[str, Any]:
    require(principal, "admin")
    rows = session.scalars(
        select(ApiKey)
        .where(ApiKey.tenant_id == principal.tenant_id)
        .order_by(ApiKey.created_at.desc())
    )
    return {
        "items": [
            {
                "id": key.id,
                "name": key.name,
                "role": key.role,
                "revoked": key.revoked,
                "expires_at": key.expires_at,
            }
            for key in rows
        ]
    }


@router.post("/keys", status_code=201, tags=["identity"])
def new_key(
    body: KeyCreate, session: SessionDep, principal: PrincipalDep, settings: SettingsDep
) -> dict[str, Any]:
    require(principal, "admin")
    lock_tenant(session, principal.tenant_id)
    count = (
        session.scalar(
            select(func.count())
            .select_from(ApiKey)
            .where(
                ApiKey.tenant_id == principal.tenant_id,
                ApiKey.revoked.is_(False),
                ApiKey.expires_at > now(),
            )
        )
        or 0
    )
    if count >= 50:
        raise HTTPException(409, "Maximum 50 active keys per tenant")
    key, token = issue_key(
        session, principal.tenant_id, body.name, body.role, settings.api_key_days
    )
    audit(session, principal, "iam.key.created", key.id)
    return {
        "id": key.id,
        "token": token,
        "expires_at": key.expires_at,
        "notice": "Shown once. Store securely.",
    }


@router.delete("/keys/{key_id}", status_code=204, tags=["identity"])
def revoke_key(key_id: str, session: SessionDep, principal: PrincipalDep) -> None:
    require(principal, "admin")
    lock_tenant(session, principal.tenant_id)
    key = session.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.tenant_id == principal.tenant_id)
    )
    if not key:
        raise HTTPException(404, "Key not found")
    if key.id == principal.key_id:
        raise HTTPException(409, "Use a different owner key to revoke this session's key")
    key.revoked = True
    audit(session, principal, "iam.key.revoked", key.id)


@router.get("/secrets", tags=["secrets"])
def secrets_list(
    session: SessionDep, principal: PrincipalDep, limit: Limit = 50, offset: Offset = 0
) -> dict[str, Any]:
    rows = session.scalars(
        select(Secret)
        .where(Secret.tenant_id == principal.tenant_id)
        .order_by(Secret.name)
        .offset(offset)
        .limit(limit)
    )
    return {
        "items": [
            {
                "id": item.id,
                "name": item.name,
                "version": item.version,
                "created_at": item.created_at,
                "managed": item.name.startswith("db-"),
            }
            for item in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@router.put("/secrets", tags=["secrets"])
def write_secret(
    body: SecretWrite, session: SessionDep, principal: PrincipalDep, settings: SettingsDep
) -> dict[str, Any]:
    require(principal, "secret")
    lock_tenant(session, principal.tenant_id)
    if body.name.startswith("db-"):
        raise HTTPException(409, "The db- prefix is reserved for managed database credentials")
    item = session.scalar(
        select(Secret).where(Secret.tenant_id == principal.tenant_id, Secret.name == body.name)
    )
    encrypted = Fernet(settings.encryption_key.encode()).encrypt(body.value.encode())
    if item:
        item.ciphertext, item.version = encrypted, item.version + 1
    else:
        count = (
            session.scalar(
                select(func.count())
                .select_from(Secret)
                .where(Secret.tenant_id == principal.tenant_id)
            )
            or 0
        )
        if count >= 100:
            raise HTTPException(409, "Secret quota exceeded")
        item = Secret(tenant_id=principal.tenant_id, name=body.name, ciphertext=encrypted)
        session.add(item)
    session.flush()
    audit(session, principal, "secret.written", item.id)
    return {"id": item.id, "name": item.name, "version": item.version}


@router.post("/secrets/{secret_id}/reveal", tags=["secrets"])
def reveal_secret(
    secret_id: str, session: SessionDep, principal: PrincipalDep, settings: SettingsDep
) -> dict[str, Any]:
    require(principal, "secret")
    item = session.scalar(
        select(Secret).where(Secret.id == secret_id, Secret.tenant_id == principal.tenant_id)
    )
    if not item:
        raise HTTPException(404, "Secret not found")
    audit(session, principal, "secret.revealed", item.id)
    return {
        "value": Fernet(settings.encryption_key.encode()).decrypt(item.ciphertext).decode(),
        "version": item.version,
    }


@router.delete("/secrets/{secret_id}", status_code=204, tags=["secrets"])
def remove_secret(secret_id: str, session: SessionDep, principal: PrincipalDep) -> None:
    require(principal, "secret")
    lock_tenant(session, principal.tenant_id)
    item = session.scalar(
        select(Secret).where(Secret.id == secret_id, Secret.tenant_id == principal.tenant_id)
    )
    if not item:
        raise HTTPException(404, "Secret not found")
    if item.name.startswith("db-"):
        raise HTTPException(409, "Delete the associated database to remove managed credentials")
    audit(session, principal, "secret.deleted", item.id)
    session.delete(item)


@router.get("/billing", tags=["billing"])
def billing(session: SessionDep, principal: PrincipalDep) -> dict[str, Any]:
    rates = {"compute": 12000, "database": 20000, "loadbalancer": 8000, "bucket": 0}
    rows = session.scalars(select(Resource).where(Resource.tenant_id == principal.tenant_id))
    items = []
    for item in rows:
        seconds = max(0, (item.deleted_at or now()) - item.ready_at) if item.ready_at else 0
        hourly = rates[item.kind] * item.spec.get("cpu", 1)
        items.append(
            {
                "resource_id": item.id,
                "name": item.name,
                "kind": item.kind,
                "seconds": seconds,
                "hourly_microusd": hourly,
                "amount_microusd": seconds * hourly // 3600,
            }
        )
    return {
        "simulation": True,
        "currency": "USD",
        "unit": "microUSD (1 USD = 1000000)",
        "total_microusd": sum(item["amount_microusd"] for item in items),
        "items": items,
        "notice": "Synthetic elapsed-time estimates, never charges. Includes time until deletion; excludes storage, traffic, taxes and actual runtime usage.",
    }


@router.get("/metrics", tags=["monitoring"])
def metrics(session: SessionDep, principal: PrincipalDep) -> Response:
    lines = [
        "# HELP clouder_resources Tenant resource count by kind and control-plane state",
        "# TYPE clouder_resources gauge",
    ]
    rows = session.execute(
        select(Resource.kind, Resource.state, func.count())
        .where(Resource.tenant_id == principal.tenant_id)
        .group_by(Resource.kind, Resource.state)
    )
    for kind, state, count in rows:
        lines.append(f'clouder_resources{{kind="{kind}",state="{state}"}} {count}')
    lines.extend(
        [
            "# HELP clouder_operations Tenant operation count by state",
            "# TYPE clouder_operations gauge",
        ]
    )
    for state, count in session.execute(
        select(Operation.state, func.count())
        .where(Operation.tenant_id == principal.tenant_id)
        .group_by(Operation.state)
    ):
        lines.append(f'clouder_operations{{state="{state}"}} {count}')
    oldest = session.scalar(
        select(func.min(Operation.created_at)).where(
            Operation.tenant_id == principal.tenant_id, Operation.state.in_(["queued", "running"])
        )
    )
    lines.extend(
        [
            "# HELP clouder_oldest_operation_seconds Age of oldest unfinished tenant operation",
            "# TYPE clouder_oldest_operation_seconds gauge",
            f"clouder_oldest_operation_seconds {max(0, now() - oldest) if oldest else 0}",
        ]
    )
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
