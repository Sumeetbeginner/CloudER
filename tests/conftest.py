import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text

from clouder.app import create_app
from clouder.config import Settings
from clouder.models import Tenant
from clouder.security import issue_key
from clouder.worker import Worker


@pytest.fixture
def platform(tmp_path: Path) -> Iterator[tuple]:
    url = os.getenv("TEST_DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    settings = Settings(
        _env_file=None,
        database_url=url,
        data_dir=tmp_path,
        encryption_key=Fernet.generate_key().decode(),
        resource_quota=5,
        storage_quota_bytes=100,
        object_limit_bytes=64,
    )
    app = create_app(settings)
    db = app.state.db
    schema = "test_" + uuid.uuid4().hex
    if url.startswith("postgresql"):
        # Each test gets its own PostgreSQL schema, also exercising production SQL.
        from sqlalchemy import event

        with db.engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        db.engine.dispose()

        @event.listens_for(db.engine, "connect")
        def search_path(connection, _):
            previous = connection.autocommit
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute(f'SET search_path TO "{schema}"')
            connection.autocommit = previous

    db.migrate()
    credentials = {}
    with db.transaction() as session:
        for name in ("alpha", "beta"):
            tenant = Tenant(name=name)
            session.add(tenant)
            session.flush()
            credentials[name + "_id"] = tenant.id
            for role in ("owner", "operator", "viewer"):
                _, token = issue_key(session, tenant.id, f"{name}-{role}", role, 30)
                credentials[name + "_" + role] = {"Authorization": "Bearer " + token}
    with TestClient(app) as client:
        yield client, db, Worker(db, settings), credentials, settings
    if url.startswith("postgresql"):
        with db.engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    db.engine.dispose()


def provision(client, headers, name="example", kind="compute", key=None, spec=None):
    return client.post(
        "/v1/resources",
        headers={**headers, "Idempotency-Key": key or str(uuid.uuid4())},
        json={"name": name, "kind": kind, "spec": spec or {}},
    )


def ready_resource(platform, name="example", kind="compute", spec=None):
    client, db, worker, credentials, settings = platform
    result = provision(client, credentials["alpha_owner"], name, kind, spec=spec)
    assert result.status_code == 202, result.text
    worker.run_once()
    return result.json()["resource_id"]
