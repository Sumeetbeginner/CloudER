# ADR 0001: Modular control plane with a database-backed job queue

Status: accepted for the engineering preview.

## Context

CloudER must not lose accepted provisioning work, leak another tenant's resources, or create duplicates when a client retries. The initial deployment is a single host with a small operator team. There is no measured traffic or availability requirement justifying multiple independently consistent services.

## Decision

Use typed Python/FastAPI, SQLAlchemy, PostgreSQL, a separate worker process, and a provider protocol. Keep job publication in the same transaction as resource creation. Claim jobs using short transactions and PostgreSQL skip-locked selection; perform effects outside transactions and condition completion on lease ownership. Keep SQLite only for local development. Use a dependency-free browser console while the API contracts evolve.

## Alternatives

- **FastAPI background tasks:** simpler, but accepted work disappears on process restart.
- **Redis/Celery immediately:** useful at scale, but a second durability boundary needs an outbox and operational ownership before it solves this workload better.
- **Many microservices:** independent deployments, but distributed transactions, API contracts, and authentication overhead would dominate this initial system.
- **Kubernetes as the first provider:** powerful scheduling and mature reconciliation, but obscures the foundational cloud-control-plane behavior this project is intended to demonstrate. Add behind the same provider contract after real Docker evidence.
- **Go control plane:** a reasonable alternative for runtime-heavy services. Python offers fast iteration and a mature validation/testing stack; no performance claim is made without measurements. A future node agent can use Go without rewriting the domain API.

## Consequences

Per-tenant writes serialize. Queue history needs retention and index management. Lease expiry allows duplicate execution; providers must remain idempotent. SQL fencing cannot fence arbitrary remote effects. The console has no framework build chain but needs disciplined event handling and browser coverage. Split services only after profiling identifies a boundary with independent scaling, reliability, or ownership needs.
