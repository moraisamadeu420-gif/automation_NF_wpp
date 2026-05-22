"""
app/database/connection.py
SQLAlchemy async engine e session factory.

SQLite (dev):    DATABASE_URL=sqlite+aiosqlite:///./data/nfse.db
PostgreSQL (prod): DATABASE_URL=postgresql+asyncpg://user:pass@host/dbname
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    pass


_is_sqlite = settings.database_url.startswith("sqlite")

_engine_kwargs: dict = {
    "echo": settings.debug,
    "pool_pre_ping": True,  # reconecta em conexões mortas (essencial em produção)
}

if _is_sqlite:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # Connection pool para PostgreSQL
    # pool_size: conexões persistentes; max_overflow: burst máximo
    _engine_kwargs.update({
        "pool_size": 5,
        "max_overflow": 10,
        "pool_timeout": 30,
        "pool_recycle": 1800,  # recicla conexões a cada 30 min (evita timeout do PG)
    })

engine = create_async_engine(settings.database_url, **_engine_kwargs)

AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db() -> None:
    """Aplica PRAGMAs do SQLite na inicialização. PostgreSQL não precisa disso."""
    if _is_sqlite:
        async with engine.begin() as conn:
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA synchronous=NORMAL"))


async def create_tables() -> None:
    """Cria tabelas direto pelo ORM. Usado em testes e dev sem Alembic."""
    async with engine.begin() as conn:
        if _is_sqlite:
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA synchronous=NORMAL"))
        await conn.run_sync(Base.metadata.create_all)


async def drop_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
