import json

import httpx
import pytest

from clouder.models import Resource, uid
from clouder.providers import DockerProvider, ProviderError


def resource(kind="compute"):
    return Resource(
        id=uid(),
        tenant_id=uid(),
        kind=kind,
        name="test",
        spec={"cpu": 1, "memory_mb": 512, "targets": []},
    )


def test_catalog_security_and_database_config(platform):
    provider = DockerProvider(platform[4])
    for kind in ("compute", "database", "loadbalancer"):
        item = resource(kind)
        if kind == "loadbalancer":
            item.spec["targets"] = [uid()]
        config = provider.container_config(item, "test-password")
        assert config["User"] != "0"
        assert config["HostConfig"]["ReadonlyRootfs"] is True
        assert config["HostConfig"]["CapDrop"] == ["ALL"]
        assert config["HostConfig"]["Memory"] == 512 * 1024 * 1024
        assert "no-new-privileges:true" in config["HostConfig"]["SecurityOpt"]
        assert config["Labels"]["clouder.tenant"] == item.tenant_id
        if kind == "loadbalancer":
            assert config["HostConfig"]["PortBindings"]["8080/tcp"][0]["HostIp"] == "127.0.0.1"
            assert item.spec["targets"][0] in config["Env"][0]
        else:
            assert "PortBindings" not in config["HostConfig"]
    with pytest.raises(ProviderError, match="credentials"):
        provider.container_config(resource("database"), None)


def test_docker_reconcile_is_idempotent_and_label_guarded(platform):
    provider = DockerProvider(platform[4])
    item = resource("loadbalancer")
    item.spec["targets"] = [uid()]
    exists = False
    creates = []

    def handler(request):
        nonlocal exists
        if request.url.path.endswith("/containers/create"):
            creates.append(json.loads(request.content))
            exists = True
            return httpx.Response(201, json={"Id": "container"})
        if request.url.path.endswith("/json"):
            return (
                httpx.Response(
                    200,
                    json={
                        "Config": {
                            "Labels": {
                                "clouder.resource": item.id,
                                "clouder.tenant": item.tenant_id,
                            }
                        },
                        "State": {"Running": True},
                        "NetworkSettings": {"Ports": {"8080/tcp": [{"HostPort": "49123"}]}},
                    },
                )
                if exists
                else httpx.Response(404)
            )
        if "/networks/" in request.url.path:
            return httpx.Response(200, json={"Labels": {"clouder.tenant": item.tenant_id}})
        if request.method == "DELETE":
            exists = False
        return httpx.Response(204)

    provider.client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://docker/v1.47"
    )
    assert provider.apply(item, "create")["endpoint"] == "http://127.0.0.1:49123"
    assert provider.apply(item, "create")["provider"] == "docker"
    assert len(creates) == 1
    assert provider.apply(item, "delete")["provider"] == "docker"
    assert provider.apply(item, "delete")["provider"] == "docker"
    provider.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"Config": {"Labels": {}}})
        ),
        base_url="http://docker",
    )
    with pytest.raises(ProviderError, match="ownership"):
        provider.apply(item, "delete")


def test_runtime_errors_never_expose_response_body(platform):
    provider = DockerProvider(platform[4])
    provider.client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(500, text="password=do-not-leak")),
        base_url="http://docker",
    )
    with pytest.raises(ProviderError) as caught:
        provider.call("GET", "/containers/test/json")
    assert "do-not-leak" not in str(caught.value)
