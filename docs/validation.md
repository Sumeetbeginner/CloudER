# Validation evidence

Updated: 2026-10-05. This file records actual checks, not aspirational production claims.

| Check | Result | Scope / limitation |
|---|---|---|
| Ruff lint and format | Pass | Python source, scripts, and tests |
| Strict mypy | Pass | 15 Python source modules |
| JavaScript parse | Pass | Console JavaScript |
| Python behavioral suite | 41 passed, 1 skipped | SQLite; 94.74% measured statement coverage after worker-liveness changes |
| Docker adapter contracts | Pass | Mocked Docker HTTP responses and security configuration assertions; not live Docker |
| Desktop browser | Pass | Chromium, 1440 px; console → HTTP API → SQL/jobs → worker → rendered readiness |
| Mobile browser | Pass | Chromium, 390 px; responsive layout, navigation, no page-level horizontal overflow |
| Browser data flow | Pass | Compute provisioning, bucket creation, object upload/download byte equality, audited secret reveal, all navigation sections, sign-out |
| Browser/log safety | Pass | No console errors; no local/session storage use; test token and test secret absent from process logs |
| PostgreSQL integration | Pass in GitHub Actions | Full non-Docker behavioral suite on PostgreSQL 17 |
| Real Docker integration | Pass in GitHub Actions | HTTP through HAProxy to nginx; PostgreSQL data persists across restart; idempotent creation |
| Compose startup / image build | Pass in GitHub Actions | Image build, migration ordering, host ingress, PostgreSQL metadata, packaged worker, object upload/download/delete |
| Production load/chaos/restore | Not performed | No performance, availability, RPO/RTO, or production security certification is claimed |

Browser verification caught a modern HTML-pattern incompatibility with an unescaped hyphen. The resource/key/secret name patterns were corrected and the complete flow passed afterward. Review also corrected transaction dependency scope so accepted HTTP responses follow commit, moved credential-decryption failures into the bounded worker retry path, and fixed the rootless socket mapping in the Docker override.

The optional local agent-browser daemon could not start in the execution environment. The committed Playwright test performed equivalent direct Chromium verification with the API and worker launched in the same test process environment. No public deployment or screenshot mock was used.

Reproduce:

```bash
pip install -c requirements.lock -e '.[dev]'
npm ci
make check
make test
npx playwright install chromium
npm run test:e2e
```

GitHub Actions independently defines quality, PostgreSQL, real Docker, image build, and browser jobs. Inspect the actual run conclusion before treating an external integration gate as passed. A skip is not a pass.

GitHub run [37216146949](https://github.com/Sumeetbeginner/CloudER/actions/runs/37216146949) passed quality, PostgreSQL, and browser jobs. Its real-Docker job identified a load-balancer endpoint missing because the container used only an internal network. The correction adds a separate tenant ingress network, preserves internal-only compute/database placement, validates loopback binding before reporting readiness, and applies the same separation to Compose API ingress.

After the correction, GitHub run [37285038195](https://github.com/Sumeetbeginner/CloudER/actions/runs/37285038195) passed all four jobs (**quality, postgres, browser, docker**) on implementation commit `fb89d037cd5511f3badae2ab41ecc58dfdd8068e`. The Docker job passed the real nginx/HAProxy/PostgreSQL integration, application image build, and complete packaged Compose smoke test. This evidence does not establish high availability, hostile-tenant isolation, or managed backup/restore.

## Worker reliability increment

Local `make check` and `make test` pass: 41 tests passed, one live-Docker test skipped, 94.74% statement coverage. New cases cover background renewal beyond the original lease deadline, token/expiry fencing after database lock waits, lost database ownership, deadline overrun, graceful drain, provider-scoped health and CLI exit codes, real subprocess death after a deterministic provider effect, and v1→v2 schema migration with injected DDL failure rollback. The subprocess uses a test provider, not Docker.

GitHub run [37355595267](https://github.com/Sumeetbeginner/CloudER/actions/runs/37355595267) passed all four jobs (**quality, postgres, browser, docker**) on implementation commit `d9d6d29c017c35c0b98810260a90cb204d8fd50e`. PostgreSQL ran 41 passing tests, including lock-wait expiry, process-kill recovery, and migration rollback. The Docker job passed real workloads and the packaged Compose smoke test, which now invokes the operator health command against the deployed worker. Desktop/mobile browser flows also passed. The following documentation-only commit records these results.

Schema v2 is additive but requires a stopped-writer upgrade; see [the runbook](operations.md#schema-v1--v2-upgrade-and-rollback). This increment does not add runtime-enforced fencing, automatic drift repair, backup/PITR, or an enterprise availability guarantee.
