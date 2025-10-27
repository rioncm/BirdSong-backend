from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Set

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from lib.config import DatabaseConfig

from .tables import metadata


MigrationFn = Callable[[Connection], None]


@dataclass(frozen=True)
class Migration:
    version: str
    upgrade: MigrationFn


_MIGRATIONS: List[Migration] = []
_ENGINE: Optional[Engine] = None
_SESSION_FACTORY: Optional[sessionmaker] = None


def register_migration(version: str, upgrade: MigrationFn) -> None:
    """Register a migration step; versions must be unique."""
    if any(m.version == version for m in _MIGRATIONS):
        raise ValueError(f"Migration '{version}' already registered")
    _MIGRATIONS.append(Migration(version, upgrade))
    _MIGRATIONS.sort(key=lambda m: m.version)


def _get_database_file(config: DatabaseConfig) -> Path:
    database_dir = Path(config.path)
    database_dir.mkdir(parents=True, exist_ok=True)
    return database_dir / config.name


def _sqlite_connect_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[override]
    """Apply pragmas that keep SQLite sturdy and fast."""
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def _prepare_engine(config: DatabaseConfig, *, echo: bool = False) -> Engine:
    engine_name = (config.engine or "sqlite").lower()
    if engine_name != "sqlite":
        raise ValueError(f"Unsupported database engine '{config.engine}'")

    db_file = _get_database_file(config)
    engine = create_engine(
        f"sqlite:///{db_file}",
        future=True,
        echo=echo,
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _sqlite_connect_pragmas)
    return engine


def _ensure_schema_table(connection: Connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )


def _applied_versions(connection: Connection) -> Set[str]:
    result = connection.execute(text("SELECT version FROM schema_migrations"))
    return {row[0] for row in result}


def _record_version(connection: Connection, version: str) -> None:
    connection.execute(
        text("INSERT INTO schema_migrations(version) VALUES (:version)"),
        {"version": version},
    )


def run_migrations(engine: Engine) -> None:
    """Apply any outstanding migrations."""
    migrations = list(_MIGRATIONS)
    if not migrations:
        return

    with engine.begin() as connection:
        _ensure_schema_table(connection)
        applied = _applied_versions(connection)
        for migration in migrations:
            if migration.version in applied:
                continue
            migration.upgrade(connection)
            _record_version(connection, migration.version)


def init_engine(config: DatabaseConfig, *, echo: bool = False) -> Engine:
    """Create (or return) the configured SQLAlchemy Engine."""
    global _ENGINE, _SESSION_FACTORY

    if _ENGINE is not None:
        return _ENGINE

    engine = _prepare_engine(config, echo=echo)
    run_migrations(engine)

    _SESSION_FACTORY = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )
    _ENGINE = engine
    return engine


def get_engine() -> Engine:
    if _ENGINE is None:
        raise RuntimeError("Database engine is not initialized")
    return _ENGINE


def get_session() -> Session:
    if _SESSION_FACTORY is None:
        raise RuntimeError("Session factory is not initialized")
    return _SESSION_FACTORY()


def initialize_database(config: DatabaseConfig, *, echo: bool = False) -> Engine:
    """
    Public entry point: ensure engine exists, run migrations,
    and return a ready-to-use Engine instance.
    """
    engine = init_engine(config, echo=echo)
    return engine


def _initial_schema(connection: Connection) -> None:
    metadata.create_all(connection)


def _column_exists(connection: Connection, table: str, column: str) -> bool:
    result = connection.execute(text(f'PRAGMA table_info("{table}")'))
    return any(row[1] == column for row in result)


def _upgrade_0002_days_metadata(connection: Connection) -> None:
    if not _column_exists(connection, "days", "forecast_issued_at"):
        connection.execute(text("ALTER TABLE days ADD COLUMN forecast_issued_at TIMESTAMP"))
    if not _column_exists(connection, "days", "forecast_source"):
        connection.execute(text("ALTER TABLE days ADD COLUMN forecast_source VARCHAR(128)"))
    if not _column_exists(connection, "days", "actual_updated_at"):
        connection.execute(text("ALTER TABLE days ADD COLUMN actual_updated_at TIMESTAMP"))
    if not _column_exists(connection, "days", "actual_source"):
        connection.execute(text("ALTER TABLE days ADD COLUMN actual_source VARCHAR(128)"))


def _upgrade_0003_recordings_source_metadata(connection: Connection) -> None:
    if not _column_exists(connection, "recordings", "source_id"):
        connection.execute(text("ALTER TABLE recordings ADD COLUMN source_id VARCHAR(128)"))
    if not _column_exists(connection, "recordings", "source_name"):
        connection.execute(text("ALTER TABLE recordings ADD COLUMN source_name VARCHAR(255)"))
    if not _column_exists(connection, "recordings", "source_display_name"):
        connection.execute(text("ALTER TABLE recordings ADD COLUMN source_display_name VARCHAR(255)"))
    if not _column_exists(connection, "recordings", "source_location"):
        connection.execute(text("ALTER TABLE recordings ADD COLUMN source_location VARCHAR(255)"))


def _upgrade_0004_species_summary(connection: Connection) -> None:
    columns = set()
    result = connection.execute(text('PRAGMA table_info("species")'))
    for row in result:
        columns.add(row._mapping["name"])

    if "summary" not in columns and "ai_summary" in columns:
        connection.execute(text("ALTER TABLE species RENAME COLUMN ai_summary TO summary"))
    elif "summary" not in columns:
        connection.execute(text("ALTER TABLE species ADD COLUMN summary TEXT"))


# Register migrations at import time.
register_migration("0001_initial", _initial_schema)
register_migration("0002_days_metadata", _upgrade_0002_days_metadata)
register_migration("0003_recordings_source_metadata", _upgrade_0003_recordings_source_metadata)
register_migration("0004_species_summary", _upgrade_0004_species_summary)
