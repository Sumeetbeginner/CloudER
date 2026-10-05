# ADR 0003: Renewable execution leases and operator liveness

Status: accepted for the single-host engineering preview.

## Problem

A fixed lease can expire during a slow provider call. Another worker may then claim the operation while the first is still executing. Previously, an expired worker could also commit its result if no replacement had changed its token yet. An API readiness check cannot tell an operator whether any worker is polling or executing jobs.

## Decision

Renew each active claim on a dedicated thread, outside provider execution. Renewal and completion both condition their writes on a running operation, the matching token, and an unexpired lease. The expiry timestamp is sampled after acquiring the operation write lock, so a long database lock wait cannot revive an expired claim. Renewal and the busy heartbeat commit together. An initial renewal failure prevents the provider call. A later failure marks ownership uncertain and discards its eventual result. Shutdown stops the polling loop after its current call and keeps renewal active while draining.

Renewal is bounded by a monotonic execution deadline (600 seconds by default). A timed-out call cannot publish its result or keep renewing indefinitely. Its last lease must expire before recovery. This deadline does **not** interrupt Python, forcibly stop the provider, or cancel an in-flight Docker request. A stuck process needs operator termination; Compose supplies a configurable shutdown grace period before its normal forced termination. The process may remain blocked even after renewal stops.

A schema-v2 table records one UUID per worker process, provider mode, idle/busy/stopped state, timestamps, and its current operation/token. These are operator snapshots, not tenant-visible data or permanent audit history. Startup removes snapshots older than 24 hours. `clouder worker-health` returns aggregate JSON for the configured provider. A busy heartbeat is live only while its corresponding claim is valid and still matches. The command exits 1 if no live worker exists or metadata/schema access fails. It is a **pool** signal, not an individual container healthcheck or a backlog/capacity guarantee.

## Alternatives

- Increase a fixed TTL: postpones the race and increases crash recovery latency without establishing liveness.
- Renew in the provider thread: cannot renew while that thread is blocked.
- External queue/coordination service: adds a consistency boundary without a measured need; SQL already owns claims.
- Treat heartbeat as runtime fencing: unsafe. Docker does not enforce our token, and a stalled or partitioned worker may still produce effects.

## Consequences and assumptions

The worker owns one operation at a time and creates a short-lived renewal thread per call. SQL transactions remain short; runtime effects remain outside them. Timestamps persisted in leases/heartbeats use host wall clocks, so all control-plane hosts must synchronize clocks. The execution deadline uses monotonic time. Clock jumps, database stalls, and process pauses can still trigger duplicate execution; deterministic provider identities and idempotent effects remain mandatory. There is no exactly-once or hostile-worker guarantee.

Schema v2 is additive but old binaries require v1: use a stopped-writer upgrade, not a mixed-version rollout. Back up metadata, object data, and the encryption key before migration. Restore the pre-upgrade snapshot for rollback; do not decrement the schema marker while new workers are running. See the operator runbook.

## Evidence

Behavioral tests cover renewal beyond the original deadline, old-token/expired-token rejection, database-error redaction, execution overruns, graceful drain, health classification, CLI exit codes, and interrupted migration rollback. A subprocess test kills a worker after a deterministic test-provider effect, then verifies expired-lease recovery with one resource identity. These tests run against SQLite and the PostgreSQL CI service. Live Docker remains a separate adapter gate; this is not a complete production chaos experiment.
