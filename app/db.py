from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _connect_args(database_url: str) -> dict[str, object]:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


settings = get_settings()
engine = create_engine(settings.database_url, connect_args=_connect_args(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_sqlite_users_table()
    _migrate_sqlite_user_aliases()


def _migrate_sqlite_users_table() -> None:
    if engine.dialect.name != "sqlite":
        return

    legacy_column = "_".join(("display", "name"))
    inspector = inspect(engine)
    if not inspector.has_table("users"):
        return
    if legacy_column not in {column["name"] for column in inspector.get_columns("users")}:
        return

    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE users DROP COLUMN {legacy_column}"))


def _migrate_sqlite_user_aliases() -> None:
    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)
    if not inspector.has_table("users"):
        return
    if "canonical_user_id" in {column["name"] for column in inspector.get_columns("users")}:
        return

    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE users ADD COLUMN canonical_user_id INTEGER REFERENCES users(id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_users_canonical_user_id ON users (canonical_user_id)"))
