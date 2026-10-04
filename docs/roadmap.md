# From engineering preview to a credible senior-engineering case study

Completion means reproducible evidence, not feature names in a README. The following targets are proposed acceptance criteria, not achieved results.

| Milestone | Deliverables | Evidence required to call it complete |
|---|---|---|
| 1. Control-plane correctness | Tenant isolation, RBAC, transactional jobs, idempotency, leases, encrypted secrets, quotas | Concurrent requests, transaction rollback, crash-after-effect recovery, cross-tenant negative tests on PostgreSQL |
| 2. Runtime reliability | Generation-aware desired state, worker heartbeat, drift scans, start/stop/update, cancellation, stronger fencing | Kill workers at every lifecycle boundary; reconcile orphaned/stopped containers without duplication |
| 3. Real managed data | S3-compatible backend adapter, physical-disk accounting/GC, PostgreSQL backup/restore and PITR | Restore drills into fresh hosts; object corruption detection; measured RPO/RTO; credential rotation without lost connectivity |
| 4. Identity and isolation | OIDC/SSO, short-lived sessions, policy engine, RLS, workload network policy, external KMS | Threat-model review; policy decision tests; key-rotation/recovery exercise; escape/isolation assessment |
| 5. Multi-node compute | Node agent, scheduler, capacity inventory, heartbeats, placement constraints, stronger workload sandbox | Node-loss experiments, stale-agent fencing, capacity exhaustion, placement fairness, scale measurements |
| 6. Operations | OpenTelemetry, worker/host/workload metrics, dashboards, alert delivery, error budgets | Load-test report with hardware/config, p50/p95/p99, saturation curves, one incident review and runbook-driven recovery |
| 7. Billing ledger | Immutable usage events, versioned prices, deduplication, settlement, reconciliation | Replay identical events without double-charge; handle late events; compare ledger totals to independent calculation |
| 8. Release engineering | Immutable image/action pins, SBOM, dependency scans, signed images, migration matrix, upgrade/rollback | Reproducible release build, verified provenance, staged rollout, successful restore and rollback drill |

## Suggested performance experiment

Use a fixed disposable PostgreSQL host, fixed API/worker replicas, and record CPU, RAM, disk, Python/PostgreSQL/Docker versions, catalog digests, and source commit. Test reads separately from resource acceptance and provisioning. Use realistic mixed tenants; include one noisy tenant. Report saturation and failures, not only the fastest run. Compare one worker versus several workers to evaluate queue contention. Tenant write serialization is an explicit hypothesis to measure.

Do not publish a claim such as “10,000 requests/second,” “99.99% availability,” or “enterprise security” unless the corresponding evidence exists. Use planned targets only when labeled as targets.

## Interview-ready engineering artifacts

1. Explain an architectural decision and the alternative rejected, using the ADRs.
2. Demonstrate the control-plane transaction and why an in-memory background task loses accepted work.
3. Kill a worker after container creation, recover the expired job, and show one runtime object.
4. Show a denied cross-tenant access attempt and a concurrent quota test.
5. Demonstrate a real database restore, then explain the remaining RPO/RTO risks.
6. Present a measured performance bottleneck, the change made, and before/after evidence.
7. Write an incident review: impact, timeline, contributing factors, recovery, and follow-up ownership.
8. Describe where the system intentionally limits functionality to preserve safety and consistency.

The current repository supplies the control-plane foundation, adapter contracts, tests, architecture decisions, and runbooks. It does not replace sustained operational ownership or guarantee any hiring level. The strongest portfolio story is the sequence of measured improvements and decisions you personally understand and can defend.
