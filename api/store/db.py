"""Database engine and session.

STAND-IN by Person 1 so the system runs end to end. Person 4 owns api/store/ and may
replace any file here; keep the public function names and signatures.
"""

from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from api.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def ensure_database() -> None:
    """Create the dockergpt_memory database if it does not exist yet."""
    url = make_url(settings.database_url)
    if not url.drivername.startswith("postgresql"):
        return
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT", future=True)
    with admin.connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database}).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


def init_db() -> None:
    from api.store import models  # noqa: F401  (registers tables)

    ensure_database()
    Base.metadata.create_all(engine)


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
