"""Bounded object uploads. Logical keys never become filesystem paths."""

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select

from clouder.dependencies import PrincipalDep, SessionDep, SettingsDep
from clouder.models import Resource, StoredObject, now
from clouder.routes import Limit, Offset
from clouder.security import audit, require
from clouder.services import lock_tenant, owned_resource

router = APIRouter(prefix="/v1/buckets", tags=["object storage"])


def bucket_for(session: SessionDep, principal: PrincipalDep, bucket_id: str) -> Resource:
    bucket = owned_resource(session, principal, bucket_id)
    if bucket.kind != "bucket" or bucket.state != "ready":
        raise HTTPException(409, "Bucket is not ready")
    return bucket


def blob_path(root: Path, tenant_id: str, digest: str) -> Path:
    return root / "objects" / tenant_id / digest[:2] / digest


@router.get("/{bucket_id}/objects")
def list_objects(
    bucket_id: str,
    session: SessionDep,
    principal: PrincipalDep,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict[str, Any]:
    bucket_for(session, principal, bucket_id)
    rows = session.scalars(
        select(StoredObject)
        .where(StoredObject.tenant_id == principal.tenant_id, StoredObject.bucket_id == bucket_id)
        .order_by(StoredObject.key)
        .offset(offset)
        .limit(limit)
    )
    return {
        "items": [
            {
                "key": item.key,
                "size": item.size,
                "sha256": item.sha256,
                "created_at": item.created_at,
            }
            for item in rows
        ],
        "limit": limit,
        "offset": offset,
    }


@router.put("/{bucket_id}/objects/{key:path}")
async def put_object(
    bucket_id: str,
    key: str,
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    require(principal, "write")
    if not key or len(key) > 512 or any(ord(char) < 32 for char in key):
        raise HTTPException(422, "Object key must contain 1–512 printable characters")
    # Read before obtaining the tenant write lock so slow uploads don't block quotas.
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > settings.object_limit_bytes:
            raise HTTPException(413, "Object exceeds the upload limit")
        data.extend(chunk)
    tenant = lock_tenant(session, principal.tenant_id)
    bucket_for(session, principal, bucket_id)
    item = session.scalar(
        select(StoredObject).where(
            StoredObject.bucket_id == bucket_id,
            StoredObject.key == key,
            StoredObject.tenant_id == principal.tenant_id,
        )
    )
    delta = len(data) - (item.size if item else 0)
    if tenant.storage_bytes + delta > settings.storage_quota_bytes:
        raise HTTPException(409, "Tenant storage quota exceeded")
    digest = hashlib.sha256(data).hexdigest()
    path = blob_path(settings.data_dir, principal.tenant_id, digest)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Publish the immutable blob before metadata. A DB failure may leave an orphan,
    # but cannot produce a committed object referring to a partially written blob.
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    if item:
        item.sha256, item.size, item.created_at = digest, len(data), now()
    else:
        session.add(
            StoredObject(
                tenant_id=principal.tenant_id,
                bucket_id=bucket_id,
                key=key,
                sha256=digest,
                size=len(data),
            )
        )
    tenant.storage_bytes += delta
    audit(session, principal, "object.put", bucket_id)
    return {"key": key, "size": len(data), "sha256": digest}


@router.get("/{bucket_id}/objects/{key:path}")
def get_object(
    bucket_id: str, key: str, session: SessionDep, principal: PrincipalDep, settings: SettingsDep
) -> Response:
    bucket_for(session, principal, bucket_id)
    item = session.scalar(
        select(StoredObject).where(
            StoredObject.bucket_id == bucket_id,
            StoredObject.key == key,
            StoredObject.tenant_id == principal.tenant_id,
        )
    )
    if not item:
        raise HTTPException(404, "Object not found")
    try:
        content = blob_path(settings.data_dir, principal.tenant_id, item.sha256).read_bytes()
    except FileNotFoundError as exc:
        raise HTTPException(503, "Object blob is unavailable; contact the operator") from exc
    if hashlib.sha256(content).hexdigest() != item.sha256:
        raise HTTPException(503, "Object integrity verification failed")
    return Response(
        content,
        media_type="application/octet-stream",
        headers={"ETag": f'"{item.sha256}"', "Content-Disposition": "attachment"},
    )


@router.delete("/{bucket_id}/objects/{key:path}", status_code=204)
def delete_object(bucket_id: str, key: str, session: SessionDep, principal: PrincipalDep) -> None:
    require(principal, "write")
    tenant = lock_tenant(session, principal.tenant_id)
    bucket_for(session, principal, bucket_id)
    item = session.scalar(
        select(StoredObject).where(
            StoredObject.bucket_id == bucket_id,
            StoredObject.key == key,
            StoredObject.tenant_id == principal.tenant_id,
        )
    )
    if not item:
        raise HTTPException(404, "Object not found")
    tenant.storage_bytes -= item.size
    session.delete(item)
    audit(session, principal, "object.deleted", bucket_id)
    # Blobs are intentionally retained until offline GC; concurrent readers remain safe.
