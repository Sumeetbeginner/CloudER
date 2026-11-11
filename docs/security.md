# Security model and limitations

## Assets and trust boundaries

Protect tenant metadata, stored objects, secret plaintext, database data, runtime credentials, and the host. Treat browsers and tenant API inputs as untrusted. The operator, control-plane process, control-plane database administrator, and Docker administrator are trusted in this preview. A compromised worker can control its Docker host.

| Threat | Implemented control | Remaining exposure / next control |
|---|---|---|
| Cross-tenant object/resource access | Principal-derived tenant filters and ownership checks; foreign IDs return 404 | Add PostgreSQL RLS as defense in depth, including connection-pool context tests |
| Privilege escalation | Server-side RBAC; only owners manage keys | OIDC, MFA, group mapping and a policy decision layer |
| API-key disclosure | Random 256-bit token, digest-only persistence, expiry and revocation; browser memory only | TLS termination, short-lived sessions, SSO; avoid copying keys into logs/history |
| Secret disclosure from DB backup | Fernet authenticated encryption | External KMS/Vault, envelope encryption, key rotation/recovery drills |
| Secret disclosure in errors/logs | Validation inputs stripped; sanitized provider errors; no headers/bodies logged | Structured denied-access audit and log-redaction regression suite for new paths |
| Container escape / host compromise | Restricted catalog; non-root workloads; dropped capabilities; resource limits | Dedicated hosts, rootless compatibility validation, seccomp/AppArmor review, stronger isolation for hostile tenants |
| SSRF / runtime command injection | LB targets are owned compute IDs; no arbitrary URL/image/command/host-mount fields | Future features must preserve these restrictions or introduce explicit policy checks |
| Quota bypass by concurrent requests | Tenant write lock around usage mutation | CPU/memory aggregate quotas, physical-disk quota, per-tenant rate limits |
| Path traversal | Object keys map to metadata; filesystem path uses UUID + digest only | Operator filesystem access remains trusted |
| Stored script execution | Object downloads forced to attachment/octet-stream; escaped console output; same-origin CSP | Ongoing browser XSS regression coverage |
| Request flood | Bounded payloads, paginated lists, worker retry budget | Mandatory reverse-proxy rate/connection limits before any remote exposure |
| Queue replay / worker crash | Stable runtime names, idempotent effects, renewable leases, expiry-aware completion fencing, operator heartbeats | Strong runtime fencing, periodic drift scans |
| Audit tampering | No audit-update/delete API; events committed with mutations | Append-only DB grants, external immutable log sink, retention policy |
| Supply-chain compromise | Locked Python resolution; dependency update automation; restricted runtime catalog | Digest-pinned images, signed provenance, vulnerability gate and SBOM |

## Important limits

- This is not ready for public internet exposure or untrusted customer tenants.
- HTTP defaults to loopback. Configure TLS, trusted-proxy behavior, authentication policy, and rate limits before adding remote ingress.
- All workloads within a tenant share one internal network. Only the controlled HAProxy template also joins a tenant-specific non-internal ingress network; it consequently has an outbound route. The API similarly joins a dedicated Compose ingress network. There are no per-resource security groups or additional egress policies.
- PostgreSQL passwords are encrypted in control-plane storage but appear in trusted Docker container configuration. Docker administrators can access them.
- The API's readiness check covers metadata/schema connectivity, not worker health, storage health, or workload uptime.
- Logical storage quota excludes retained blobs. Deleting objects does not immediately reclaim disk.
- Database deletion explicitly destroys its named volume after removing the container. The console requires confirmation and describes the data loss.
- API keys and secret values displayed once in the console are not written to localStorage/sessionStorage. Browser extensions or a compromised browser can still read an active page.
- Bootstrap is an operator CLI action with direct database access. There is no unauthenticated bootstrap HTTP endpoint.

Report vulnerabilities privately to the repository owner. Do not include live tokens, secret plaintext, or unrelated tenant data in public issues.
