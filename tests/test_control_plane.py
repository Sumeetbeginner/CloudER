from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from clouder.models import ApiKey, Audit, Idempotency, Operation, Resource
from clouder.schemas import CreateResource
from clouder.security import Principal
from clouder.services import create_resource
from tests.conftest import provision, ready_resource


def test_auth_health_and_validation(platform):
    client, _, _, keys, _ = platform
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert client.get("/v1/resources").status_code == 401
    assert (
        client.get("/v1/resources", headers={"Authorization": "Bearer invalid"}).status_code == 401
    )
    result = provision(client, keys["alpha_owner"], name="BAD NAME")
    assert result.status_code == 422
    assert "input" not in result.text
    assert (
        client.post(
            "/v1/resources", headers=keys["alpha_owner"], json={"name": "test", "kind": "compute"}
        ).status_code
        == 422
    )
    assert client.get("/").headers["X-Content-Type-Options"] == "nosniff"


def test_lifecycle_and_idempotency(platform):
    client, db, worker, keys, _ = platform
    first = provision(client, keys["alpha_owner"], key="same-request-key")
    again = provision(client, keys["alpha_owner"], key="same-request-key")
    assert first.status_code == 202 and first.json() == again.json()
    assert first.headers["location"].endswith(first.json()["id"])
    assert (
        provision(client, keys["alpha_owner"], name="other", key="same-request-key").status_code
        == 409
    )
    assert worker.run_once()
    assert not worker.run_once()
    resource_id = first.json()["resource_id"]
    resource = client.get(f"/v1/resources/{resource_id}", headers=keys["alpha_owner"]).json()
    assert resource["state"] == "ready"
    assert resource["output"]["provider"] == "simulated"
    deleted = client.delete(
        f"/v1/resources/{resource_id}",
        headers={**keys["alpha_owner"], "Idempotency-Key": "delete-resource-key"},
    )
    assert deleted.status_code == 202
    assert worker.run_once()
    assert (
        client.get(f"/v1/resources/{resource_id}", headers=keys["alpha_owner"]).json()["state"]
        == "deleted"
    )
    assert (
        client.delete(
            f"/v1/resources/{resource_id}",
            headers={**keys["alpha_owner"], "Idempotency-Key": "delete-resource-key"},
        ).json()["id"]
        == deleted.json()["id"]
    )
    with db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Resource)) == 1
        assert session.scalar(select(func.count()).select_from(Operation)) == 2
        assert session.scalar(select(func.count()).select_from(Audit)) == 4


def test_tenant_isolation_and_rbac(platform):
    client, _, worker, keys, _ = platform
    first = provision(client, keys["alpha_owner"])
    resource_id, operation_id = first.json()["resource_id"], first.json()["id"]
    assert provision(client, keys["alpha_viewer"]).status_code == 403
    assert client.get(f"/v1/resources/{resource_id}", headers=keys["beta_owner"]).status_code == 404
    assert (
        client.get(f"/v1/operations/{operation_id}", headers=keys["beta_owner"]).status_code == 404
    )
    assert client.get("/v1/resources", headers=keys["beta_owner"]).json()["items"] == []
    assert client.get("/v1/audit", headers=keys["beta_owner"]).json()["items"] == []
    assert client.get("/v1/summary", headers=keys["beta_owner"]).json()["active_resources"] == 0
    assert (
        client.delete(
            f"/v1/resources/{resource_id}",
            headers={**keys["beta_owner"], "Idempotency-Key": "foreign-delete-key"},
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/v1/resources/{resource_id}",
            headers={**keys["alpha_owner"], "Idempotency-Key": "pending-delete-key"},
        ).status_code
        == 409
    )
    worker.run_once()
    assert client.get("/v1/resources", headers=keys["alpha_viewer"]).status_code == 200
    assert provision(client, keys["alpha_operator"], name="operator-created").status_code == 202


def test_quota_duplicate_name_and_filtering(platform):
    client, _, _, keys, _ = platform
    for index in range(5):
        assert provision(client, keys["alpha_owner"], name=f"instance-{index}").status_code == 202
    assert provision(client, keys["alpha_owner"], name="instance-extra").status_code == 409
    assert provision(client, keys["beta_owner"], name="instance-0").status_code == 202
    assert provision(client, keys["beta_owner"], name="instance-0").status_code == 409
    assert (
        client.get("/v1/resources?kind=database", headers=keys["alpha_owner"]).json()["items"] == []
    )
    assert (
        len(
            client.get("/v1/resources?limit=2&offset=2", headers=keys["alpha_owner"]).json()[
                "items"
            ]
        )
        == 2
    )


def test_create_is_atomic_and_concurrent_idempotency(platform):
    _, db, _, keys, settings = platform
    with db.transaction() as session:
        key = session.scalar(
            select(ApiKey).where(ApiKey.tenant_id == keys["alpha_id"], ApiKey.role == "owner")
        )
        principal = Principal(key.id, key.tenant_id, key.role)

    def invoke(_):
        with db.transaction() as session:
            return create_resource(
                session,
                settings,
                principal,
                CreateResource(name="concurrent", kind="compute"),
                "concurrent-key",
            ).id

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(invoke, range(5)))
    assert len(set(results)) == 1
    with db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Resource)) == 1
        assert session.scalar(select(func.count()).select_from(Operation)) == 1
        assert session.scalar(select(func.count()).select_from(Idempotency)) == 1
    try:
        with db.transaction() as session:
            create_resource(
                session,
                settings,
                principal,
                CreateResource(name="rollback", kind="compute"),
                "rollback-key",
            )
            raise RuntimeError("crash before commit")
    except RuntimeError:
        pass
    with db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Resource)) == 1


def test_concurrent_quota_cannot_overshoot(platform):
    _, db, _, keys, settings = platform
    from fastapi import HTTPException

    principal = Principal("test", keys["alpha_id"], "owner")

    def invoke(index):
        try:
            with db.transaction() as session:
                create_resource(
                    session,
                    settings,
                    principal,
                    CreateResource(name=f"race-{index}", kind="compute"),
                    f"quota-race-{index}",
                )
            return 202
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(invoke, range(8)))
    assert results.count(202) == 5
    assert results.count(409) == 3


def test_load_balancer_dependencies(platform):
    client, _, worker, keys, _ = platform
    target = ready_resource(platform)
    balancer = provision(
        client, keys["alpha_owner"], name="gateway", kind="loadbalancer", spec={"targets": [target]}
    )
    assert balancer.status_code == 202
    assert (
        provision(
            client,
            keys["beta_owner"],
            name="bad-gateway",
            kind="loadbalancer",
            spec={"targets": [target]},
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/v1/resources/{target}",
            headers={**keys["alpha_owner"], "Idempotency-Key": "blocked-delete-key"},
        ).status_code
        == 409
    )
    worker.run_once()


def test_keys_revocation_expiry_and_self_protection(platform):
    client, db, _, keys, _ = platform
    body = {"name": "readonly", "role": "viewer"}
    assert client.post("/v1/keys", headers=keys["alpha_operator"], json=body).status_code == 403
    created = client.post("/v1/keys", headers=keys["alpha_owner"], json=body).json()
    credential = {"Authorization": "Bearer " + created["token"]}
    assert client.get("/v1/me", headers=credential).status_code == 200
    listing = client.get("/v1/keys", headers=keys["alpha_owner"])
    assert created["token"] not in listing.text and "digest" not in listing.text
    assert client.delete(f"/v1/keys/{created['id']}", headers=keys["beta_owner"]).status_code == 404
    assert (
        client.delete(f"/v1/keys/{created['id']}", headers=keys["alpha_owner"]).status_code == 204
    )
    assert client.get("/v1/me", headers=credential).status_code == 401
    own = client.get("/v1/me", headers=keys["alpha_owner"]).json()["key_id"]
    assert client.delete(f"/v1/keys/{own}", headers=keys["alpha_owner"]).status_code == 409
    with db.transaction() as session:
        session.get(ApiKey, own).expires_at = 1
    assert client.get("/v1/me", headers=keys["alpha_owner"]).status_code == 401


def test_billing_and_metrics(platform):
    client, db, _, keys, _ = platform
    resource_id = ready_resource(platform)
    with db.transaction() as session:
        resource = session.get(Resource, resource_id)
        resource.ready_at = 1000
        resource.deleted_at = 4600
        resource.state = "deleted"
    bill = client.get("/v1/billing", headers=keys["alpha_owner"]).json()
    assert bill["simulation"] is True
    assert bill["total_microusd"] == 12000
    assert client.get("/v1/billing", headers=keys["beta_owner"]).json()["total_microusd"] == 0
    metrics = client.get("/v1/metrics", headers=keys["alpha_viewer"])
    assert 'clouder_resources{kind="compute",state="deleted"} 1' in metrics.text
    assert "clouder_oldest_operation_seconds" in metrics.text
