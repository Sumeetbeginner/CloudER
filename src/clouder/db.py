from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from clouder.config import Settings
from clouder.models import Base, WorkerHeartbeat

SCHEMA_VERSION = 2


class Database:
    def __init__(self, settings: Settings):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        sqlite = settings.database_url.startswith("sqlite")
        self.engine: Engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False, "timeout": 30} if sqlite else {},
        )
        if sqlite:

            @event.listens_for(self.engine, "connect")
            def configure_sqlite(connection: object, _: object) -> None:
                cursor = connection.cursor()  # type: ignore[attr-defined]
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def migrate(self) -> None:
        """Explicit forward migrations, serialized and atomic on both backends."""
        with self.engine.begin() as connection:
            # Serialize migration runners in PostgreSQL; SQLite is local-development only.
            if self.engine.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_xact_lock(73419201)"))
            else:
                # sqlite3 legacy transaction mode does not BEGIN for DDL itself.
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            connection.execute(
                text("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
            )
            version = connection.execute(text("SELECT MAX(version) FROM schema_version")).scalar()
            if version is None:
                Base.metadata.create_all(
                    connection,
                    tables=[
                        t for t in Base.metadata.sorted_tables if t.name != "worker_heartbeats"
                    ],
                )
                connection.execute(text("INSERT INTO schema_version (version) VALUES (1)"))
                version = 1
            if version == 1:
                Base.metadata.tables[WorkerHeartbeat.__tablename__].create(connection)
                connection.execute(text("INSERT INTO schema_version (version) VALUES (2)"))
            elif version != SCHEMA_VERSION:
                raise RuntimeError("Unsupported schema version; use the matching CloudER release")

    def require_schema(self) -> None:
        with self.transaction() as session:
            version = session.execute(text("SELECT MAX(version) FROM schema_version")).scalar()
            if version != SCHEMA_VERSION:
                raise RuntimeError(
                    "Schema mismatch; run clouder migrate with matching processes stopped"
                )

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        with self.sessions.begin() as session:
            yield session
