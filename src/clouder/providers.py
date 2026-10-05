"""Idempotent provider boundary. Only workers may instantiate DockerProvider."""

from typing import Any, Protocol

import httpx

from clouder.config import Settings
from clouder.models import Resource


class ProviderError(Exception):
    """Public, sanitized provider failure; never include a raw runtime response."""


class Provider(Protocol):
    def apply(
        self, resource: Resource, action: str, password: str | None = None
    ) -> dict[str, Any]: ...


class SimulatedProvider:
    def apply(self, resource: Resource, action: str, password: str | None = None) -> dict[str, Any]:
        return {"provider": "simulated", "notice": "Lifecycle simulation; no workload is running"}


class DockerProvider:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            transport=httpx.HTTPTransport(uds=settings.docker_socket),
            base_url=f"http://docker/{settings.docker_api}",
            timeout=20,
        )

    def call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ProviderError("Container runtime is unreachable") from exc
        if response.status_code >= 400 and response.status_code != 404:
            raise ProviderError(
                f"Container runtime rejected {method} request ({response.status_code})"
            )
        return response

    @staticmethod
    def name(resource: Resource) -> str:
        return f"clouder-{resource.id}"

    def network(self, tenant_id: str, *, ingress: bool = False) -> str:
        # Docker cannot publish ports for an internal-only container. Only the
        # controlled proxy joins a non-internal, tenant-specific ingress network.
        name = f"clouder-{'ingress' if ingress else 'tenant'}-{tenant_id}"
        current = self.call("GET", f"/networks/{name}")
        if current.status_code == 404:
            self.call(
                "POST",
                "/networks/create",
                json={
                    "Name": name,
                    "Internal": not ingress,
                    "Labels": {"clouder.tenant": tenant_id},
                },
            )
        elif current.json().get("Labels", {}).get("clouder.tenant") != tenant_id:
            raise ProviderError("Network ownership label mismatch")
        elif current.json().get("Internal") != (not ingress):
            raise ProviderError("Network isolation configuration mismatch")
        return name

    def container_config(self, resource: Resource, password: str | None) -> dict[str, Any]:
        network = f"clouder-tenant-{resource.tenant_id}"
        host: dict[str, Any] = {
            "Memory": resource.spec["memory_mb"] * 1024 * 1024,
            "NanoCpus": resource.spec["cpu"] * 1_000_000_000,
            "PidsLimit": 128,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "ReadonlyRootfs": True,
            "NetworkMode": network,
            "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=64m"},
            "LogConfig": {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}},
            "RestartPolicy": {"Name": "unless-stopped"},
        }
        config: dict[str, Any] = {
            "Labels": {"clouder.resource": resource.id, "clouder.tenant": resource.tenant_id},
            "HostConfig": host,
            "NetworkingConfig": {"EndpointsConfig": {network: {"Aliases": [self.name(resource)]}}},
        }
        if resource.kind == "compute":
            config.update(
                Image=self.settings.compute_image, User="101:101", ExposedPorts={"8080/tcp": {}}
            )
        elif resource.kind == "database":
            if not password:
                raise ProviderError("Database credentials are missing")
            # Numeric postgres user for the supported alpine catalog image; named volume is
            # populated from image metadata by Docker. Never accept arbitrary host mounts.
            config.update(
                Image=self.settings.database_image,
                User="70:70",
                Env=[
                    "POSTGRES_USER=clouder",
                    "POSTGRES_DB=clouder",
                    f"POSTGRES_PASSWORD={password}",
                ],
                ExposedPorts={"5432/tcp": {}},
            )
            host["Binds"] = [f"clouder-data-{resource.id}:/var/lib/postgresql/data"]
            host["Tmpfs"]["/var/run/postgresql"] = "rw,nosuid,size=16m,uid=70,gid=70"
        elif resource.kind == "loadbalancer":
            ingress = f"clouder-ingress-{resource.tenant_id}"
            host["NetworkMode"] = ingress
            config["NetworkingConfig"]["EndpointsConfig"][ingress] = {}
            config.update(
                Image=self.settings.loadbalancer_image,
                User="99:99",
                ExposedPorts={"8080/tcp": {}},
                Env=["CLOUDER_PROXY_CONFIG=" + self.balancer_config(resource)],
                Cmd=[
                    "sh",
                    "-ec",
                    'printf "%s" "$CLOUDER_PROXY_CONFIG" > /tmp/haproxy.cfg; exec haproxy -W -db -f /tmp/haproxy.cfg',
                ],
            )
            host["PortBindings"] = {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "0"}]}
        else:
            raise ProviderError("Unsupported Docker resource kind")
        return config

    @staticmethod
    def balancer_config(resource: Resource) -> str:
        servers = "\n".join(
            f"    server backend{i} clouder-{target}:8080 check"
            for i, target in enumerate(resource.spec["targets"])
        )
        return f"global\n    log stdout format raw local0\ndefaults\n    log global\n    mode http\n    timeout connect 5s\n    timeout client 30s\n    timeout server 30s\nfrontend ingress\n    bind :8080\n    default_backend nodes\nbackend nodes\n    balance roundrobin\n{servers}\n"

    def apply(self, resource: Resource, action: str, password: str | None = None) -> dict[str, Any]:
        if resource.kind == "bucket":
            return {"provider": "local", "storage": "content-addressed-files"}
        name = self.name(resource)
        current = self.call("GET", f"/containers/{name}/json")
        if current.status_code != 404:
            labels = current.json().get("Config", {}).get("Labels", {})
            if (
                labels.get("clouder.resource") != resource.id
                or labels.get("clouder.tenant") != resource.tenant_id
            ):
                raise ProviderError("Container ownership label mismatch")
        if action == "delete":
            if current.status_code != 404:
                self.call("DELETE", f"/containers/{name}", params={"force": "true"})
            if resource.kind == "database":
                self.call("DELETE", f"/volumes/clouder-data-{resource.id}")
            return {"provider": "docker"}
        self.network(resource.tenant_id)
        if resource.kind == "loadbalancer":
            ingress = self.network(resource.tenant_id, ingress=True)
            if current.status_code != 404 and ingress not in current.json().get(
                "NetworkSettings", {}
            ).get("Networks", {}):
                self.call("POST", f"/networks/{ingress}/connect", json={"Container": name})
        if current.status_code == 404:
            response = self.call(
                "POST",
                "/containers/create",
                params={"name": name},
                json=self.container_config(resource, password),
            )
            if response.status_code == 404:
                raise ProviderError(
                    "Catalog image missing; run scripts/pull-images.sh on the worker host"
                )
        started = self.call("POST", f"/containers/{name}/start")
        if started.status_code == 404:
            raise ProviderError("Container disappeared before start")
        actual = self.call("GET", f"/containers/{name}/json")
        if actual.status_code == 404 or not actual.json()["State"]["Running"]:
            raise ProviderError("Container is not running")
        result: dict[str, Any] = {
            "provider": "docker",
            "container": name,
            "host": name,
            "readiness": "process-running; application health not certified",
        }
        if resource.kind == "database":
            result.update(
                port=5432, database="clouder", username="clouder", secret_name=f"db-{resource.id}"
            )
        elif resource.kind == "loadbalancer":
            ports = actual.json().get("NetworkSettings", {}).get("Ports", {}).get("8080/tcp", [])
            binding = next(
                (port for port in ports or [] if port.get("HostIp") == "127.0.0.1"), None
            )
            if not binding or not str(binding.get("HostPort", "")).isdigit():
                raise ProviderError(
                    "Load balancer has no published loopback port; inspect ingress networking"
                )
            result["endpoint"] = f"http://127.0.0.1:{binding['HostPort']}"
        return result
