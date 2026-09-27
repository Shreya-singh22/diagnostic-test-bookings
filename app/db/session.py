from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def _build_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        # SQLite is only used for the fast local test run; Postgres is the real target.
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_conn, _):  # SQLite ignores FKs unless asked
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20)


engine = _build_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
