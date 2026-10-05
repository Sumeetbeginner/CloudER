"""Exercise the packaged simulation stack on a disposable Compose project.

Requires a generated .env and a dedicated CI/development Docker daemon.
Only the unique project created here is removed, including its test volumes.
"""

import subprocess
import time
import uuid

import httpx


def main() -> None:
    project = "clouder-smoke-" + uuid.uuid4().hex[:12]
    command = ["docker", "compose", "--project-name", project]

    def compose(*args: str, timeout: int = 240) -> str:
        result = subprocess.run([*command, *args], capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            # Do not print bootstrap output, expanded environment or secret values.
            raise RuntimeError(f"Compose {' '.join(args[:2])} failed (exit {result.returncode})")
        return result.stdout

    try:
        compose("up", "--build", "--wait", "--wait-timeout", "120")
        with httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False, timeout=5) as client:
            client.get("/health/ready").raise_for_status()
            assert "CloudER" in client.get("/").text
            output = compose("exec", "-T", "api", "clouder", "bootstrap", "--name", "Compose smoke")
            token = output.split("Owner API key (shown once): ", 1)[1].strip()
            client.headers["Authorization"] = "Bearer " + token
            response = client.post(
                "/v1/resources",
                headers={"Idempotency-Key": str(uuid.uuid4())},
                json={"name": "smoke-bucket", "kind": "bucket"},
            )
            response.raise_for_status()
            operation = response.json()
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                result = client.get(f"/v1/operations/{operation['id']}").json()
                if result["state"] == "succeeded":
                    break
                assert result["state"] != "failed", "Packaged worker failed provisioning"
                time.sleep(0.25)
            else:
                raise RuntimeError("Packaged worker did not complete the operation")
            path = f"/v1/buckets/{operation['resource_id']}/objects/proof.txt"
            client.put(path, content=b"packaged stack proof").raise_for_status()
            assert client.get(path).content == b"packaged stack proof"
            client.delete(path).raise_for_status()
        print("PASS: Compose ingress, migration, PostgreSQL, worker, and object round trip")
    finally:
        compose("down", "--volumes", "--remove-orphans", timeout=120)


if __name__ == "__main__":
    main()
