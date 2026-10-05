import pytest
from sqlalchemy import event, inspect, text

from clouder.models import Base, Operation, Resource
from tests.conftest import provision


def revert_to_v1(db):
    with db.engine.begin() as connection:
        connection.execute(text("DROP TABLE worker_heartbeats"))
        connection.execute(text("DELETE FROM schema_version WHERE version = 2"))


def test_v1_upgrade_preserves_resources_and_jobs(platform):
    client, db, worker, keys, _ = platform
    created = provision(client, keys["alpha_owner"]).json()
    revert_to_v1(db)
    assert client.get("/health/ready").status_code == 503
    with pytest.raises(RuntimeError, match="Schema mismatch"):
        db.require_schema()
    db.migrate()
    db.migrate()  # Idempotent second migration.
    assert client.get("/health/ready").status_code == 200
    assert worker.run_once()
    with db.transaction() as session:
        assert session.get(Resource, created["resource_id"]).state == "ready"
        assert session.get(Operation, created["id"]).state == "succeeded"
        assert session.execute(
            text("SELECT version FROM schema_version ORDER BY version")
        ).scalars().all() == [1, 2]


def test_failed_upgrade_rolls_back_ddl_and_version(platform):
    _, db, _, _, _ = platform
    revert_to_v1(db)
    table = Base.metadata.tables["worker_heartbeats"]

    def fail(*args, **kwargs):
        raise RuntimeError("injected migration failure")

    event.listen(table, "after_create", fail)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            db.migrate()
    finally:
        event.remove(table, "after_create", fail)
    assert "worker_heartbeats" not in inspect(db.engine).get_table_names()
    with db.transaction() as session:
        assert session.execute(text("SELECT MAX(version) FROM schema_version")).scalar() == 1
    db.migrate()
    db.require_schema()


def test_unknown_schema_is_rejected_without_mutation(platform):
    client, db, _, _, _ = platform
    with db.transaction() as session:
        session.execute(text("INSERT INTO schema_version (version) VALUES (99)"))
    with pytest.raises(RuntimeError, match="Unsupported schema"):
        db.migrate()
    assert client.get("/health/ready").status_code == 503
    with db.transaction() as session:
        assert session.execute(text("SELECT MAX(version) FROM schema_version")).scalar() == 99
