"""
app/database/connection.py
SQLAlchemy async engine and session factory.
Swap DATABASE_URL in .env to migrate from SQLite to PostgreSQL.
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    # SQLite-specific: enables WAL mode for concurrent reads
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
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
        await conn.run_sync(Base.metadata.create_all)


async def drop_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
