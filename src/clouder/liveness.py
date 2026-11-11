"""Operator-only worker liveness; no runtime socket or tenant API dependency."""

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from clouder.config import Settings
from clouder.db import Database
from clouder.models import Operation, WorkerHeartbeat, now


def record_heartbeat(
    session: Session,
    worker_id: str,
    provider: str,
    state: str,
    operation_id: str | None = None,
    token: str | None = None,
) -> None:
    # One execution loop owns this UUID. Its renewal thread is joined before reuse.
    heartbeat = session.get(WorkerHeartbeat, worker_id)
    if heartbeat is None:
        heartbeat = WorkerHeartbeat(id=worker_id, provider=provider)
        session.add(heartbeat)
    heartbeat.state, heartbeat.seen_at = state, now()
    heartbeat.operation_id, heartbeat.lease_token = operation_id, token


def worker_health(db: Database, settings: Settings) -> dict[str, str | int]:
    db.require_schema()
    current = now()
    fresh = WorkerHeartbeat.seen_at > current - 3 * settings.worker_heartbeat_seconds
    busy = and_(
        WorkerHeartbeat.state == "busy",
        Operation.state == "running",
        Operation.lease_token == WorkerHeartbeat.lease_token,
        Operation.lease_until > current,
    )
    with db.transaction() as session:
        row = session.execute(
            select(
                func.count().label("total"),
                func.count(case((and_(fresh, WorkerHeartbeat.state == "idle"), 1))).label("idle"),
                func.count(case((and_(fresh, busy), 1))).label("busy"),
                func.count(case((WorkerHeartbeat.state == "stopped", 1))).label("stopped"),
            )
            .select_from(WorkerHeartbeat)
            .outerjoin(Operation, WorkerHeartbeat.operation_id == Operation.id)
            .where(WorkerHeartbeat.provider == settings.provider)
        ).one()
    live = row.idle + row.busy
    return {
        "status": "healthy" if live else "unavailable",
        "provider": settings.provider,
        "live_workers": live,
        "busy_workers": row.busy,
        "stale_workers": row.total - live - row.stopped,
        "stopped_workers": row.stopped,
    }
