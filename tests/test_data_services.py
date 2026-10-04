import hashlib

from sqlalchemy import select

from clouder.models import Secret
from clouder.storage import blob_path
from tests.conftest import ready_resource


def test_object_round_trip_overwrite_and_delete(platform):
    client, _, _, keys, _ = platform
    bucket = ready_resource(platform, kind="bucket")
    path = f"/v1/buckets/{bucket}/objects/folder/hello.txt"
    content = b"hello private cloud"
    uploaded = client.put(path, headers=keys["alpha_owner"], content=content)
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["sha256"] == hashlib.sha256(content).hexdigest()
    fetched = client.get(path, headers=keys["alpha_viewer"])
    assert fetched.content == content
    assert fetched.headers["content-disposition"] == "attachment"
    assert client.get(path, headers=keys["beta_owner"]).status_code == 404
    assert (
        client.put(path, headers=keys["alpha_viewer"], content=b"unauthorized").status_code == 403
    )
    assert client.put(path, headers=keys["alpha_owner"], content=b"new").status_code == 200
    assert client.get("/v1/me", headers=keys["alpha_owner"]).json()["storage_bytes"] == 3
    assert (
        client.delete(
            f"/v1/resources/{bucket}",
            headers={**keys["alpha_owner"], "Idempotency-Key": "nonempty-bucket"},
        ).status_code
        == 409
    )
    assert client.delete(path, headers=keys["alpha_owner"]).status_code == 204
    assert client.get(path, headers=keys["alpha_owner"]).status_code == 404
    assert client.get("/v1/me", headers=keys["alpha_owner"]).json()["storage_bytes"] == 0


def test_upload_limits_and_integrity(platform):
    client, _, _, keys, settings = platform
    bucket = ready_resource(platform, kind="bucket")
    path = f"/v1/buckets/{bucket}/objects/first"
    assert client.put(path, headers=keys["alpha_owner"], content=b"a" * 65).status_code == 413
    assert client.put(path, headers=keys["alpha_owner"], content=b"a" * 60).status_code == 200
    assert (
        client.put(path + "-other", headers=keys["alpha_owner"], content=b"b" * 60).status_code
        == 409
    )
    blob_path(
        settings.data_dir, keys["alpha_id"], hashlib.sha256(b"a" * 60).hexdigest()
    ).write_bytes(b"tampered")
    assert client.get(path, headers=keys["alpha_owner"]).status_code == 503


def test_logical_object_keys_never_become_paths(platform):
    client, _, _, keys, settings = platform
    bucket = ready_resource(platform, kind="bucket")
    response = client.put(
        f"/v1/buckets/{bucket}/objects/%2E%2E%2Foutside.txt",
        headers=keys["alpha_owner"],
        content=b"safe",
    )
    assert response.status_code == 200
    assert not (settings.data_dir / "outside.txt").exists()
    assert (
        len(list((settings.data_dir / "objects").rglob(hashlib.sha256(b"safe").hexdigest()))) == 1
    )


def test_secret_encryption_versioning_and_no_leaks(platform):
    client, db, _, keys, _ = platform
    secret = "private-password-must-not-appear"
    body = {"name": "database-password", "value": secret}
    first = client.put("/v1/secrets", headers=keys["alpha_operator"], json=body)
    assert first.status_code == 200
    secret_id = first.json()["id"]
    assert (
        client.put("/v1/secrets", headers=keys["alpha_operator"], json=body).json()["version"] == 2
    )
    with db.transaction() as session:
        ciphertext = session.get(Secret, secret_id).ciphertext
        assert secret.encode() not in ciphertext
    assert (
        client.post(f"/v1/secrets/{secret_id}/reveal", headers=keys["alpha_viewer"]).status_code
        == 403
    )
    assert (
        client.post(f"/v1/secrets/{secret_id}/reveal", headers=keys["beta_owner"]).status_code
        == 404
    )
    reveal = client.post(f"/v1/secrets/{secret_id}/reveal", headers=keys["alpha_operator"])
    assert reveal.json()["value"] == secret
    assert reveal.headers["cache-control"] == "no-store"
    for path in ("/v1/secrets", "/v1/audit", "/v1/resources"):
        assert secret not in client.get(path, headers=keys["alpha_owner"]).text
    malformed = client.put(
        "/v1/secrets", headers=keys["alpha_owner"], json={**body, "unexpected": secret}
    )
    assert malformed.status_code == 422 and secret not in malformed.text
    assert client.delete(f"/v1/secrets/{secret_id}", headers=keys["alpha_owner"]).status_code == 204


def test_database_credentials_managed_lifecycle(platform):
    client, db, worker, keys, _ = platform
    resource_id = ready_resource(platform, kind="database")
    with db.transaction() as session:
        secret = session.scalar(select(Secret).where(Secret.name == f"db-{resource_id}"))
        assert secret is not None
        secret_id = secret.id
    assert (
        client.put(
            "/v1/secrets",
            headers=keys["alpha_owner"],
            json={"name": f"db-{resource_id}", "value": "invalid-reset"},
        ).status_code
        == 409
    )
    assert client.delete(f"/v1/secrets/{secret_id}", headers=keys["alpha_owner"]).status_code == 409
    assert (
        client.delete(
            f"/v1/resources/{resource_id}",
            headers={**keys["alpha_owner"], "Idempotency-Key": "database-delete"},
        ).status_code
        == 202
    )
    worker.run_once()
    assert client.get("/v1/secrets", headers=keys["alpha_owner"]).json()["items"] == []
