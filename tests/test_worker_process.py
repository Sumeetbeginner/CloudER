"""Real process death after an effect, with a deterministic test provider.

Exercises the same PostgreSQL fixture when TEST_DATABASE_URL is configured.
This is not a Docker daemon failure or a production chaos experiment.
"""

import os
import subprocess
import sys
import time

from sqlalchemy import text

from clouder.models import Operation, Resource, WorkerHeartbeat, now
from tests.conftest import provision

CHILD = """
import os
import threading
from pathlib import Path
from clouder.config import Settings
from clouder.db import Database
from clouder.worker import Worker

class BlockingProvider:
    def apply(self, resource, action, password=None):
        effect = Path(os.environ["EFFECT_DIR"]) / resource.id
        effect.mkdir(exist_ok=True)
        (effect / "created").touch()
        threading.Event().wait(60)
        return {"effect": resource.id}

settings = Settings()
Worker(Database(settings), settings, BlockingProvider()).run()
"""


def test_killed_worker_recovers_after_effect_without_duplicate_resource(platform, tmp_path):
    client, db, worker, keys, settings = platform
    created = provision(client, keys["alpha_owner"]).json()
    url = db.engine.url
    if url.get_backend_name() == "postgresql":
        with db.transaction() as session:
            schema = session.execute(text("SELECT current_schema()")).scalar_one()
        url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    env = {
        **os.environ,
        "CLOUDER_DATABASE_URL": url.render_as_string(hide_password=False),
        "CLOUDER_DATA_DIR": str(tmp_path),
        "CLOUDER_ENCRYPTION_KEY": settings.encryption_key,
        "CLOUDER_PROVIDER": "simulated",
        "EFFECT_DIR": str(tmp_path),
    }
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    marker = tmp_path / created["resource_id"] / "created"
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            assert child.poll() is None, "Worker exited before test effect"
            time.sleep(0.01)
        assert marker.exists(), "Worker never reached the effect boundary"
        with db.transaction() as session:
            operation = session.get(Operation, created["id"])
            assert operation.state == "running"
            old_token = operation.lease_token
        child.kill()
        child.wait(timeout=5)
        assert worker.claim() is None  # Death alone does not revoke an unexpired lease.
        with db.transaction() as session:
            session.get(Operation, created["id"]).lease_until = now() - 1
            for heartbeat in session.query(WorkerHeartbeat).all():
                heartbeat.seen_at = now() - 60

        class RecoveringProvider:
            def apply(self, resource, action, password=None):
                effect = tmp_path / resource.id
                effect.mkdir(exist_ok=True)
                assert (effect / "created").exists()
                return {"effect": resource.id}

        worker.provider = RecoveringProvider()
        assert worker.run_once()
        worker.finish(created["id"], old_token, {"stale": True}, None)
        with db.transaction() as session:
            assert session.get(Operation, created["id"]).attempts == 2
            resource = session.get(Resource, created["resource_id"])
            assert resource.state == "ready"
            assert resource.output == {"effect": created["resource_id"]}
        assert len(list(tmp_path.glob("*/created"))) == 1
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
