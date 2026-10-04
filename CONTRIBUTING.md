# Contributing to CloudER

Read [architecture](docs/architecture.md) and `AGENTS.md` before changing boundaries.

Use small branches and pull requests. Explain the problem, design trade-off, behavioral change, verification evidence, and rollback implications. Add an ADR for a new persistence system, service boundary, authentication mechanism, scheduler, or infrastructure provider.

Run `make check` and `make test`. Use a disposable PostgreSQL database for concurrency changes and a dedicated Docker daemon for runtime changes. Add tests for failure behavior, not only successful responses. Include desktop/mobile browser evidence for meaningful console changes.

Do not commit secrets, `.env`, runtime data, virtual environments, or screenshots containing API keys or secret values. Production claims require measured evidence. State skipped checks explicitly.

Schema v1 is a baseline migration. Future changes must add a numbered migration with compatibility and rollback instructions; `metadata.create_all` does not migrate existing tables. Keep API and worker versions compatible through deployment transitions.
