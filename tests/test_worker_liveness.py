import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from clouder import cli
from clouder.liveness import worker_health
from clouder.models import Audit, Operation, Resource, WorkerHeartbeat, now
from clouder.providers import SimulatedProvider
from clouder.worker import Worker
from tests.conftest import provision


def test_expired_lease_cannot_renew_or_finish_without_replacement(platform):
    client, db, worker, keys, _ = platform
    created = provision(client, keys["alpha_owner"]).json()
    operation_id, token = worker.claim()
    with db.transaction() as session:
        session.get(Operation, operation_id).lease_until = now() - 1
    assert not worker.renew(operation_id, token)
    worker.finish(operation_id, token, {"invalid": True}, None)
    with db.transaction() as session:
        assert session.get(Operation, operation_id).state == "running"
        assert session.get(Resource, created["resource_id"]).state == "pending"
        assert len(session.scalars(select(Audit)).all()) == 1
    assert worker.run_once()
    with db.transaction() as session:
        assert session.get(Operation, operation_id).attempts == 2
        assert session.get(Resource, created["resource_id"]).state == "ready"


def test_old_token_cannot_renew_replacement_claim(platform):
    client, db, worker, keys, settings = platform
    provision(client, keys["alpha_owner"])
    operation_id, token = worker.claim()
    with db.transaction() as session:
        session.get(Operation, operation_id).lease_until = now() - 1
    replacement = Worker(db, settings)
    _, new_token = replacement.claim()
    assert not worker.renew(operation_id, token)
    assert replacement.renew(operation_id, new_token)
    assert worker_health(db, settings)["busy_workers"] == 1
    replacement.finish(operation_id, new_token, {}, None)
    assert not replacement.renew(operation_id, new_token)


def test_lease_is_renewed_while_provider_blocks(platform, monkeypatch):
    client, db, _, keys, settings = platform
    entered, release, refreshed = threading.Event(), threading.Event(), threading.Event()
    initial_time = now()
    clock = [initial_time]
    monkeypatch.setattr("clouder.worker.now", lambda: clock[0])

    class SlowProvider(SimulatedProvider):
        def apply(self, *args):
            entered.set()
            assert release.wait(5)
            return super().apply(*args)

    worker = Worker(
        db, settings.model_copy(update={"worker_heartbeat_seconds": 0.02}), SlowProvider()
    )
    created = provision(client, keys["alpha_owner"]).json()
    renew = worker.renew

    def observed_renew(operation_id, token):
        result = renew(operation_id, token)
        if entered.is_set() and clock[0] > initial_time:
            refreshed.set()
        return result

    monkeypatch.setattr(worker, "renew", observed_renew)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker.run_once)
        try:
            assert entered.wait(5)
            clock[0] = initial_time + settings.operation_lease_seconds - 10
            assert refreshed.wait(5)
            # Pass the ORIGINAL lease deadline while the renewed claim stays valid.
            clock[0] = initial_time + settings.operation_lease_seconds + 1
            assert worker_health(db, settings)["busy_workers"] == 1
            assert Worker(db, settings).claim() is None
            with db.transaction() as session:
                op = session.get(Operation, created["id"])
                assert op.attempts == 1 and op.lease_until > clock[0]
        finally:
            release.set()
        assert future.result(timeout=5)
    assert worker_health(db, settings)["busy_workers"] == 0
    assert worker_health(db, settings)["live_workers"] == 1


@pytest.mark.parametrize("failure", ["ownership", "database"])
def test_uncertain_lease_discards_provider_result(platform, monkeypatch, caplog, failure):
    client, db, _, keys, settings = platform
    lease_failed = threading.Event()

    class DelayedProvider(SimulatedProvider):
        def apply(self, *args):
            assert lease_failed.wait(5)
            return {"must_not_commit": True}

    worker = Worker(
        db, settings.model_copy(update={"worker_heartbeat_seconds": 0.02}), DelayedProvider()
    )
    created = provision(client, keys["alpha_owner"]).json()
    renew, calls = worker.renew, 0

    def fail_renew(operation_id, token):
        nonlocal calls
        calls += 1
        if calls == 1:
            return renew(operation_id, token)
        lease_failed.set()
        if failure == "database":
            raise RuntimeError("database-password-never-log")
        return False

    monkeypatch.setattr(worker, "renew", fail_renew)
    assert worker.run_once()
    with db.transaction() as session:
        assert session.get(Operation, created["id"]).state == "running"
        assert session.get(Resource, created["resource_id"]).output == {}
    assert "operation_lease_lost" in caplog.text
    assert "database-password-never-log" not in caplog.text


def test_initial_renew_failure_does_not_call_provider(platform, monkeypatch):
    client, _, worker, keys, _ = platform
    provision(client, keys["alpha_owner"])
    monkeypatch.setattr(worker, "renew", lambda *args: False)
    monkeypatch.setattr(worker.provider, "apply", lambda *args: pytest.fail("Unowned effect"))
    assert worker.run_once()


def test_execution_deadline_releases_renewal_and_rejects_late_success(platform, monkeypatch):
    client, db, _, keys, settings = platform
    clock = [0.0]
    monkeypatch.setattr("clouder.worker.time.monotonic", lambda: clock[0])

    class OverrunProvider:
        def apply(self, *args):
            clock[0] = settings.operation_timeout_seconds + 1
            return {"late": True}

    worker = Worker(db, settings, OverrunProvider())
    created = provision(client, keys["alpha_owner"]).json()
    assert worker.run_once()
    with db.transaction() as session:
        operation = session.get(Operation, created["id"])
        assert operation.state == "running"
        assert session.get(Resource, created["resource_id"]).state == "pending"
        operation.lease_until = now() - 1
    assert Worker(db, settings).run_once()


def test_shutdown_drains_current_operation_without_claiming_next(platform):
    client, db, _, keys, settings = platform
    stop, entered, release = threading.Event(), threading.Event(), threading.Event()

    class SlowProvider(SimulatedProvider):
        def apply(self, *args):
            entered.set()
            assert release.wait(5)
            return super().apply(*args)

    first = provision(client, keys["alpha_owner"], name="first").json()
    worker = Worker(db, settings, SlowProvider())
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker.serve, stop)
        try:
            assert entered.wait(5)
            second = provision(client, keys["alpha_owner"], name="second").json()
            stop.set()
        finally:
            release.set()
        future.result(timeout=5)
    with db.transaction() as session:
        assert session.get(Operation, first["id"]).state == "succeeded"
        assert session.get(Operation, second["id"]).state == "queued"


def test_health_distinguishes_stale_stopped_and_other_provider(platform):
    _, db, worker, _, settings = platform
    assert worker_health(db, settings)["status"] == "unavailable"
    worker.heartbeat("idle")
    assert worker_health(db, settings)["live_workers"] == 1
    with db.transaction() as session:
        session.get(WorkerHeartbeat, worker.id).seen_at = (
            now() - 3 * settings.worker_heartbeat_seconds
        )
    assert worker_health(db, settings)["stale_workers"] == 1
    worker.heartbeat("stopped")
    assert worker_health(db, settings)["stopped_workers"] == 1
    other = Worker(db, settings.model_copy(update={"provider": "docker"}), SimulatedProvider())
    other.heartbeat("idle")
    assert worker_health(db, settings)["status"] == "unavailable"


def test_health_rejects_fresh_heartbeat_with_lost_claim(platform):
    client, db, worker, keys, settings = platform
    provision(client, keys["alpha_owner"])
    operation_id, token = worker.claim()
    worker.renew(operation_id, token)
    with db.transaction() as session:
        session.get(Operation, operation_id).lease_token = "different-claim"
    assert worker_health(db, settings)["stale_workers"] == 1
    assert worker_health(db, settings)["status"] == "unavailable"


def test_worker_health_cli_exit_codes_and_sanitized_errors(platform, monkeypatch, capsys):
    _, db, worker, _, settings = platform
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "Database", lambda _: db)
    monkeypatch.setattr("sys.argv", ["clouder", "worker-health"])
    for healthy in (False, True):
        if healthy:
            worker.heartbeat("idle")
        with pytest.raises(SystemExit) as result:
            cli.main()
        assert result.value.code == (0 if healthy else 1)
        assert json.loads(capsys.readouterr().out)["status"] == (
            "healthy" if healthy else "unavailable"
        )

    def broken(*args):
        raise RuntimeError("secret-database-url")

    monkeypatch.setattr(cli, "worker_health", broken)
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 1
    output = capsys.readouterr().out
    assert json.loads(output)["reason"] == "metadata_or_schema_unavailable"
    assert "secret-database-url" not in output


@pytest.mark.parametrize("action", ["renew", "finish"])
def test_expiry_is_checked_after_database_lock_wait(platform, monkeypatch, action):
    from sqlalchemy import event, update

    client, db, worker, keys, settings = platform
    created = provision(client, keys["alpha_owner"]).json()
    operation_id, token = worker.claim()
    current = now()
    clock = [current]
    monkeypatch.setattr("clouder.worker.now", lambda: clock[0])
    waiting = threading.Event()

    def before_statement(connection, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement or statement == "BEGIN IMMEDIATE":
            waiting.set()

    def invoke():
        if action == "renew":
            return worker.renew(operation_id, token)
        return worker.finish(operation_id, token, {"expired": True}, None)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.transaction() as session:
            session.execute(
                update(Operation)
                .where(Operation.id == operation_id)
                .values(lease_until=current + settings.operation_lease_seconds)
            )
            event.listen(db.engine, "before_cursor_execute", before_statement)
            future = pool.submit(invoke)
            assert waiting.wait(5)
            # The UPDATE/row lock held above delays the worker past expiry.
            clock[0] += settings.operation_lease_seconds + 1
        try:
            result = future.result(timeout=5)
        finally:
            event.remove(db.engine, "before_cursor_execute", before_statement)
    assert result is (False if action == "renew" else None)
    with db.transaction() as session:
        assert (
            session.get(Operation, operation_id).lease_until
            == current + settings.operation_lease_seconds
        )
        assert session.get(Resource, created["resource_id"]).state == "pending"
