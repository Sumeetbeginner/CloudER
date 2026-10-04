from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from clouder.config import Settings
from clouder.models import Base


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
        """Initial versioned migration. Never run implicitly during API startup."""
        with self.engine.begin() as connection:
            # Serialize migration runners in PostgreSQL; SQLite is local-development only.
            if self.engine.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_xact_lock(73419201)"))
            connection.execute(
                text("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
            )
            version = connection.execute(text("SELECT MAX(version) FROM schema_version")).scalar()
            if version is None:
                Base.metadata.create_all(connection)
                connection.execute(text("INSERT INTO schema_version (version) VALUES (1)"))
            elif version != 1:
                raise RuntimeError("Unsupported schema version; use the matching CloudER release")

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        with self.sessions.begin() as session:
            yield session
