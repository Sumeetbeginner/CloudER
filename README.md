# CloudER

**A self-hosted cloud control plane for learning and demonstrating distributed-systems engineering.**

CloudER brings compute, object storage, PostgreSQL databases, load balancing, access control, secrets, operational visibility, and simulated billing into one web console.

**Status: engineering preview v0.1.0.** This repository implements a real control plane and an opt-in single-host Docker adapter. It is not an AWS replacement, a hostile multi-tenant sandbox, or a production-certified platform. See the capability matrix and [production roadmap](docs/roadmap.md) before deploying it.

## What works

| Service | Implemented behavior | Current boundary |
|---|---|---|
| Compute | Asynchronous container creation/deletion; CPU, memory and PID limits; restricted image catalog | No virtual machines, scheduler, autoscaling, custom images, or continuous health reconciliation |
| Object storage | Private buckets, upload/download/delete, SHA-256 integrity, logical byte quotas | Local files; no S3 compatibility, replication, multipart upload, or online garbage collection |
| Databases | PostgreSQL container, persistent volume, generated encrypted credentials | Single node; backup/restore, PITR, password rotation, HA and upgrades are not managed yet |
| Load balancers | HAProxy HTTP routing with backend checks across same-tenant compute | Host-loopback ingress; no domain/TLS automation or dynamic target updates |
| IAM | Tenant-scoped expiring bearer keys, owner/operator/viewer roles, revocation | No OIDC/SSO, human accounts, custom policies, or MFA |
| Secrets | Authenticated encryption at rest, version counters, audited reveal | Operator-held encryption key; no KMS/Vault, historical versions, or automatic rotation |
| Monitoring | Authenticated Prometheus text metrics, queue age, request IDs, structured logs | Control-plane state only; no host telemetry, tracing backend, alert delivery, or uptime claims |
| Billing | Deterministic integer microUSD elapsed-time estimates | Explicit simulation; no payments or metered usage ledger |
| Console | Responsive live API console for all of the above | Keys stay in browser memory; page refresh requires reconnecting |

Simulation mode is the default: compute/database/load-balancer lifecycle is simulated, while metadata, secret storage, and object storage are real. Docker mode creates real local infrastructure. Billing is always simulated.

## Quick start: local simulation

Requirements: Python 3.12 and Node.js for the optional JavaScript syntax check. SQLite is included with Python.

```bash
git clone https://github.com/Sumeetbeginner/CloudER.git
cd CloudER
python3.12 -m venv .venv
source .venv/bin/activate
pip install -c requirements.lock -e '.[dev]'
python scripts/configure.py
clouder migrate
clouder bootstrap --name "My workspace"
clouder serve
```

In a second terminal, activate the same environment and start the worker:

```bash
source .venv/bin/activate
clouder worker
```

From another terminal, `clouder worker-health` reports worker-pool liveness as JSON (exit 0 when a worker is live, 1 otherwise). It does not measure workload uptime.

Open **http://127.0.0.1:8000** and paste the owner API key printed by bootstrap. Create a compute resource, watch its operation complete, then create a bucket and upload a file. The console reads real API responses; it contains no seeded dashboard numbers.

On Windows, activate with `.venv\Scripts\Activate.ps1`. Run Docker workloads through Docker Desktop's Linux engine/WSL2. Do not commit `.env`, API keys, database files, or object data.

## PostgreSQL-backed Compose deployment

Generate `.env` using the setup above, then:

```bash
docker compose up --build -d
docker compose run --rm api bootstrap --name "My workspace"
```

The stack runs PostgreSQL, a one-shot schema migration, the API, and a separate worker. API ingress is bound to host loopback. PostgreSQL has no published host port. The API waits for migration completion; the migration waits for database health.

### Enable real Docker workloads

Use a dedicated development host/VM. The worker gains control of the selected Docker daemon; do not point it at an unrelated production daemon. Keep the API socket-free.

1. Set `CLOUDER_PROVIDER=docker` in `.env` **before creating workloads**.
2. Pre-pull the catalog images on the worker host:

   ```bash
   sh scripts/pull-images.sh
   ```

3. Start the opt-in runtime configuration:

   ```bash
   docker compose -f compose.yaml -f compose.docker.yaml up --build -d
   docker compose -f compose.yaml -f compose.docker.yaml run --rm api bootstrap --name "Docker workspace"
   ```

Create compute first, wait for `ready`, then select it as a load-balancer target. Inspect load-balancer details for its host-loopback endpoint. PostgreSQL hostnames are resolvable only within their tenant Docker network. Database passwords are available through the audited secret-reveal flow.

For rootless Docker, set the **host socket path** `CLOUDER_DOCKER_SOCKET` for the Compose bind, while the worker still uses `/var/run/docker.sock` inside its container. See [operations](docs/operations.md). Verify cgroup resource limits on that host. Images use release tags for the development catalog; pin verified digests and run integration tests before any controlled deployment.

## API example

Set `CLOUDER_API_KEY` locally to your workspace key. Never paste a real key into a committed script.

```bash
curl -sS http://127.0.0.1:8000/v1/resources \
  -H "Authorization: Bearer $CLOUDER_API_KEY" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: example-compute-0001' \
  --data '{"name":"web-01","kind":"compute","spec":{"cpu":1,"memory_mb":512}}'
```

The API returns `202`, an operation ID, a resource ID, and a `Location` header. Poll `/v1/operations/{id}`. Repeating the request with the same key/payload returns the original operation; changing the payload with that key returns `409`.

- OpenAPI contract: `GET /openapi.json`
- Liveness/readiness: `GET /health/live`, `GET /health/ready`
- Resources/operations: `/v1/resources`, `/v1/operations`
- Objects: `/v1/buckets/{bucket_id}/objects/{key}`
- Keys/secrets/audit: `/v1/keys`, `/v1/secrets`, `/v1/audit`
- Visibility: `/v1/summary`, `/v1/metrics`, `/v1/billing`

List endpoints support bounded pagination where appropriate. Billing is a lifetime aggregate; the current API-key list is capped by a maximum of 50 active keys per tenant.

## Engineering checks

```bash
npm ci
make check
make test

# Run the behavioral suite against a disposable PostgreSQL database:
TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@localhost/TEST_DB' pytest -m 'not docker'

# On an isolated Docker daemon with catalog images already pulled:
CLOUDER_TEST_DOCKER=1 pytest tests/test_docker_integration.py -v
```

The tests exercise tenant boundaries, permissions, quota races, transaction rollback, duplicate requests, renewable leases, stale/expired worker fencing, process-kill recovery, schema upgrade/rollback, retries, object integrity, secret redaction, and API-key revocation. Real-Docker integration verifies HTTP through HAProxy to nginx and PostgreSQL data persistence across a restart. GitHub Actions runs quality, PostgreSQL, Docker integration, and image-build gates. Passing local simulation tests does not substitute for those external gates.

See [validation evidence](docs/validation.md) for what was actually run and what remains unverified.

## Read the design

- [Architecture, invariants, and trade-offs](docs/architecture.md)
- [ADR: durable jobs and service boundaries](docs/adr/0001-control-plane-and-durable-jobs.md)
- [ADR: provider trust and security](docs/adr/0002-security-and-provider-trust.md)
- [Threat model](docs/security.md)
- [Operations and recovery runbook](docs/operations.md)
- [Production milestones and interview evidence](docs/roadmap.md)
- [Contribution expectations](CONTRIBUTING.md)

## Portfolio positioning

Describe measured behavior and design decisions you can explain: why acceptance is transactional, how retries avoid duplicate workloads, where SQL fencing stops helping, how tenant boundaries are enforced, and how recovery was tested. Senior-level hiring also evaluates sustained ownership, operational judgment, collaboration, and impact. Repository breadth alone is not evidence of those qualities.
