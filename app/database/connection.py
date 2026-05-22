"""
app/database/connection.py
SQLAlchemy async engine and session factory.
Swap DATABASE_URL in .env to migrate from SQLite to PostgreSQL.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    pass


_is_sqlite = "sqlite" in settings.database_url

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def create_tables() -> None:
    """Create all tables on startup (development convenience)."""
    async with engine.begin() as conn:
        if _is_sqlite:
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA synchronous=NORMAL"))
        await conn.run_sync(Base.metadata.create_all)


async def upgrade_schema() -> None:
    """Safely add columns introduced after initial create_all.
    Silently skips columns that already exist (OperationalError = duplicate)."""
    new_columns = [
        ("invoices", "failed_at",              "DATETIME"),
        ("invoices", "failed_stage",           "VARCHAR(50)"),
        ("invoices", "screenshot_path",        "TEXT"),
        ("users",    "is_blocked",                  "BOOLEAN DEFAULT 0"),
        ("users",    "subscription_expires_at",    "DATETIME"),
        ("users",    "subscription_cancelled_at",  "DATETIME"),
        ("users",    "last_payment_id",            "VARCHAR(50)"),
    ]
    async with engine.begin() as conn:
        for table, col, col_type in new_columns:
            try:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
            except Exception:
                pass  # column already exists


async def drop_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
