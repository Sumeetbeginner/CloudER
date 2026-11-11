"""Real runtime tests. Run only on an isolated development/CI Docker daemon."""

import os
import subprocess
import time

import httpx
import pytest

from clouder.models import Resource, uid
from clouder.providers import DockerProvider

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.getenv("CLOUDER_TEST_DOCKER") != "1",
        reason="Opt in with CLOUDER_TEST_DOCKER=1 on an isolated daemon",
    ),
]


def test_live_compute_load_balancer_and_database(platform):
    settings = platform[4]
    provider = DockerProvider(settings)
    tenant = uid()

    def make(kind, targets=None):
        return Resource(
            id=uid(),
            tenant_id=tenant,
            kind=kind,
            name="integration",
            spec={"cpu": 1, "memory_mb": 512, "targets": targets or []},
        )

    compute = make("compute")
    balancer = make("loadbalancer", [compute.id])
    database = make("database")
    resources = [compute, balancer, database]
    try:
        provider.apply(compute, "create")
        provider.apply(compute, "create")
        endpoint = provider.apply(balancer, "create")["endpoint"]
        deadline = time.monotonic() + 30
        served = False
        while time.monotonic() < deadline:
            try:
                response = httpx.get(endpoint, timeout=2)
                if response.status_code == 200:
                    served = True
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        assert served, "HAProxy must reach the real nginx workload"
        provider.apply(database, "create", "test-only-database-password")
        container = provider.name(database)

        def wait_database():
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                result = subprocess.run(
                    ["docker", "exec", container, "pg_isready", "-h", "127.0.0.1", "-U", "clouder"],
                    capture_output=True,
                )
                if result.returncode == 0:
                    return
                time.sleep(0.5)
            pytest.fail("PostgreSQL did not become ready")

        wait_database()
        subprocess.run(
            [
                "docker",
                "exec",
                container,
                "psql",
                "-U",
                "clouder",
                "-c",
                "CREATE TABLE proof (value int); INSERT INTO proof VALUES (42);",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(["docker", "restart", container], check=True, capture_output=True)
        wait_database()
        result = subprocess.run(
            [
                "docker",
                "exec",
                container,
                "psql",
                "-U",
                "clouder",
                "-Atc",
                "SELECT value FROM proof",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "42"
        provider.apply(database, "create", "test-only-database-password")
    finally:
        # Only UUID names created in this test can be removed.
        for item in reversed(resources):
            provider.apply(item, "delete")
        provider.call("DELETE", f"/networks/clouder-tenant-{tenant}")
        provider.call("DELETE", f"/networks/clouder-ingress-{tenant}")
        provider.client.close()
