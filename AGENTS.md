# CloudER engineering contract

- Keep changes scoped, typed, and independently reviewable. Read docs/architecture.md first.
- Preserve API → domain → provider boundaries; the API must never access a runtime socket.
- All resource queries must be scoped by the authenticated tenant. Never trust tenant IDs in request bodies.
- Quota-changing operations take the tenant write lock. Resource + job + idempotency + audit writes commit together.
- Runtime effects must be idempotent; workers may crash after an effect but before database commit.
- Never place secret plaintext, bearer tokens, request bodies, or raw runtime errors in logs or audit events.
- Add behavioral tests for permission changes, concurrency, lifecycle changes, and failure recovery.
- Run `make check` and `make test`. Run PostgreSQL/Docker integration gates for affected adapters.
- Never describe simulation as live infrastructure, provisioning state as application health, or unmeasured targets as achieved SLOs.
- Do not change the initial schema silently. Subsequent schema changes need explicit forward migration, compatibility review, and backup/rollback instructions.
- Avoid speculative microservices and new dependencies without an ADR and concrete need.
- Before pushing, inspect for credentials, local data, and generated dependencies. Keep screenshots anonymized.
