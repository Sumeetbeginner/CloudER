from concurrent.futures import ThreadPoolExecutor

from clouder.models import Operation, Resource, now
from clouder.providers import ProviderError, SimulatedProvider
from clouder.worker import Worker
from tests.conftest import provision


def test_crash_recovery_and_stale_worker_fencing(platform):
    client, db, worker, keys, settings = platform
    created = provision(client, keys["alpha_owner"]).json()
    op_id, stale_token = worker.claim()
    with db.transaction() as session:
        session.get(Operation, op_id).lease_until = now() - 1
    replacement = Worker(db, settings)
    assert replacement.run_once()
    worker.finish(op_id, stale_token, {"stale": True}, "stale failure")
    with db.transaction() as session:
        operation = session.get(Operation, op_id)
        assert operation.state == "succeeded" and operation.attempts == 2
        resource = session.get(Resource, created["resource_id"])
        assert resource.state == "ready" and "stale" not in resource.output


def test_only_one_worker_claims_a_job(platform):
    client, _, worker, keys, _ = platform
    provision(client, keys["alpha_owner"])
    with ThreadPoolExecutor(max_workers=5) as pool:
        claims = list(pool.map(lambda _: worker.claim(), range(5)))
    assert len([claim for claim in claims if claim]) == 1


def test_retry_budget_and_sanitized_failures(platform):
    client, db, _, keys, settings = platform

    class BrokenProvider:
        def apply(self, *args):
            raise RuntimeError("secret-do-not-leak")

    worker = Worker(db, settings, BrokenProvider())
    created = provision(client, keys["alpha_owner"]).json()
    for attempt in range(settings.max_attempts):
        assert worker.run_once()
        with db.transaction() as session:
            operation = session.get(Operation, created["id"])
            assert "secret-do-not-leak" not in operation.error
            if attempt < settings.max_attempts - 1:
                assert operation.state == "queued"
                operation.available_at = now() - 1
    result = client.get(f"/v1/operations/{created['id']}", headers=keys["alpha_owner"]).json()
    assert result["state"] == "failed"
    assert not worker.run_once()


def test_transient_failure_then_recovery(platform):
    client, db, _, keys, settings = platform

    class FlakyProvider(SimulatedProvider):
        calls = 0

        def apply(self, *args):
            self.calls += 1
            if self.calls == 1:
                raise ProviderError("Temporary runtime failure")
            return super().apply(*args)

    worker = Worker(db, settings, FlakyProvider())
    created = provision(client, keys["alpha_owner"]).json()
    assert worker.run_once()
    assert not worker.run_once()
    with db.transaction() as session:
        session.get(Operation, created["id"]).available_at = now() - 1
    assert worker.run_once()
    assert (
        client.get(f"/v1/operations/{created['id']}", headers=keys["alpha_owner"]).json()["state"]
        == "succeeded"
    )
