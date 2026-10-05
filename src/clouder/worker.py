"""At-least-once execution with bounded retries and fenced completion."""

import logging
import signal
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import and_, delete, or_, select, text, update
from sqlalchemy.orm import Session

from clouder.config import Settings
from clouder.db import Database
from clouder.liveness import record_heartbeat
from clouder.models import Audit, Operation, Resource, Secret, WorkerHeartbeat, now, uid
from clouder.providers import DockerProvider, Provider, ProviderError, SimulatedProvider

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, db: Database, settings: Settings, provider: Provider | None = None):
        self.db, self.settings = db, settings
        self.id = uid()
        self.provider = provider or (
            DockerProvider(settings) if settings.provider == "docker" else SimulatedProvider()
        )

    def claim(self) -> tuple[str, str] | None:
        current, token = now(), uid()
        with self.db.transaction() as session:
            eligible = or_(
                and_(Operation.state == "queued", Operation.available_at <= current),
                and_(Operation.state == "running", Operation.lease_until <= current),
            )
            query = (
                select(Operation.id)
                .where(eligible)
                .order_by(Operation.created_at, Operation.id)
                .limit(1)
            )
            if self.db.engine.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            operation_id = session.scalar(query)
            if not operation_id:
                return None
            # Conditional update also prevents duplicate claims in SQLite.
            claimed = session.execute(
                update(Operation)
                .where(Operation.id == operation_id, eligible)
                .values(
                    state="running",
                    lease_token=token,
                    lease_until=current + self.settings.operation_lease_seconds,
                    attempts=Operation.attempts + 1,
                )
            )
            return (operation_id, token) if claimed.rowcount == 1 else None  # type: ignore[attr-defined]

    def heartbeat(self, state: str) -> None:
        with self.db.transaction() as session:
            record_heartbeat(session, self.id, self.settings.provider, state)

    def renew(self, operation_id: str, token: str) -> bool:
        with self.db.transaction() as session:
            self.lock_claim(session, operation_id)
            current = now()
            renewed = session.execute(
                update(Operation)
                .where(
                    Operation.id == operation_id,
                    Operation.state == "running",
                    Operation.lease_token == token,
                    Operation.lease_until > current,
                )
                .values(lease_until=current + self.settings.operation_lease_seconds)
            )
            if renewed.rowcount != 1:  # type: ignore[attr-defined]
                return False
            record_heartbeat(session, self.id, self.settings.provider, "busy", operation_id, token)
        return True

    def lock_claim(self, session: Session, operation_id: str) -> None:
        # Sample expiry AFTER obtaining the write lock: a blocked statement must
        # not renew/complete using a timestamp captured before its lock wait.
        if self.db.engine.dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        else:
            session.execute(
                select(Operation.id).where(Operation.id == operation_id).with_for_update()
            )

    @contextmanager
    def maintain_lease(self, operation_id: str, token: str) -> Iterator[threading.Event]:
        stopped, lost = threading.Event(), threading.Event()
        deadline = time.monotonic() + self.settings.operation_timeout_seconds

        def refresh() -> bool:
            try:
                valid = time.monotonic() < deadline and self.renew(operation_id, token)
            except Exception:
                # Fail closed on uncertain ownership. Never include database errors/secrets.
                valid = False
            if not valid:
                lost.set()
                logger.warning("operation_lease_lost id=%s", operation_id)
            return valid

        def keep_alive() -> None:
            while not stopped.wait(self.settings.worker_heartbeat_seconds):
                if not refresh():
                    return

        if not refresh():
            yield lost
            return
        thread = threading.Thread(target=keep_alive, name="clouder-lease", daemon=True)
        thread.start()
        try:
            yield lost
        finally:
            stopped.set()
            thread.join()
            if time.monotonic() >= deadline:
                lost.set()

    def run_once(self) -> bool:
        self.heartbeat("idle")
        claim = self.claim()
        if not claim:
            return False
        operation_id, token = claim
        with self.maintain_lease(operation_id, token) as lost:
            if lost.is_set():
                return True
            with self.db.transaction() as session:
                operation = session.get(Operation, operation_id)
                assert operation is not None
                resource = session.get(Resource, operation.resource_id)
                assert resource is not None
                ciphertext = None
                if resource.kind == "database" and operation.action == "create":
                    secret = session.scalar(
                        select(Secret).where(
                            Secret.tenant_id == resource.tenant_id,
                            Secret.name == f"db-{resource.id}",
                        )
                    )
                    if secret:
                        ciphertext = secret.ciphertext
            error, output = None, {}
            try:
                if operation.attempts > self.settings.max_attempts:
                    raise ProviderError("Retry budget exhausted after worker interruptions")
                password = (
                    Fernet(self.settings.encryption_key.encode()).decrypt(ciphertext).decode()
                    if ciphertext
                    else None
                )
                output = self.provider.apply(resource, operation.action, password)
            except ProviderError as exc:
                error = str(exc)
            except Exception:
                logger.error("provider_unexpected_error operation=%s", operation_id)
                error = "Unexpected provider failure; inspect worker health"
        # A lost/uncertain lease cannot commit, even if the provider subsequently succeeds.
        if not lost.is_set():
            self.finish(operation_id, token, output, error)
        self.heartbeat("idle")
        return True

    def finish(
        self, operation_id: str, token: str, output: dict[str, Any], error: str | None
    ) -> None:
        with self.db.transaction() as session:
            # Fence old workers before writing either resource or job state.
            self.lock_claim(session, operation_id)
            fenced = session.execute(
                update(Operation)
                .where(
                    Operation.id == operation_id,
                    Operation.lease_token == token,
                    Operation.state == "running",
                    Operation.lease_until > now(),
                )
                .values(lease_until=0)
            )
            if fenced.rowcount != 1:  # type: ignore[attr-defined]
                return
            operation = session.get(Operation, operation_id)
            assert operation is not None
            resource = session.get(Resource, operation.resource_id)
            assert resource is not None
            operation.error = error
            if error:
                if operation.attempts < self.settings.max_attempts:
                    operation.state = "queued"
                    operation.available_at = now() + min(60, 2**operation.attempts)
                else:
                    operation.state, resource.state = "failed", "failed"
            else:
                operation.state = "succeeded"
                resource.state = "ready" if operation.action == "create" else "deleted"
                resource.output = output
                if operation.action == "create":
                    resource.ready_at = now()
                else:
                    resource.deleted_at = now()
                    if resource.kind == "database":
                        secret = session.scalar(
                            select(Secret).where(
                                Secret.tenant_id == resource.tenant_id,
                                Secret.name == f"db-{resource.id}",
                            )
                        )
                        if secret:
                            session.delete(secret)
            session.add(
                Audit(
                    tenant_id=resource.tenant_id,
                    actor="worker",
                    action=f"{resource.kind}.{operation.action}.{operation.state}",
                    target=resource.id,
                )
            )
        logger.info(
            "operation_finished id=%s result=%s",
            operation_id,
            "retry_or_failed" if error else "succeeded",
        )

    def run(self) -> None:
        self.db.require_schema()
        # Heartbeats are operational snapshots, not permanent audit history.
        with self.db.transaction() as session:
            session.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.seen_at < now() - 86400))
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        try:
            self.serve(stop)
        finally:
            try:
                self.heartbeat("stopped")
            except Exception:
                logger.warning("worker_shutdown_heartbeat_failed")

    def serve(self, stop: threading.Event) -> None:
        """Drain the current call on shutdown; keep renewing until it returns."""
        while not stop.is_set():
            try:
                worked = self.run_once()
            except Exception:
                logger.error(
                    "worker_iteration_failed; check database connectivity and encryption key"
                )
                worked = False
            if not worked:
                stop.wait(1)
