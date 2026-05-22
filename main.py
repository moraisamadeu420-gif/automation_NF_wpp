"""
main.py
FastAPI application factory e entry point.

Dev:   make dev
Prod:  uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
Docker: docker compose up
"""
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.api.middleware import RateLimitMiddleware, RequestLoggingMiddleware
from app.api.routes import admin, health, mercadopago, webhook
from app.core.config import settings
from app.core.logging import configure_logging
from app.database.connection import create_tables, init_db
from app.workers.scheduler import create_scheduler


def _run_migrations() -> None:
    """Executa migrações Alembic em thread separada (evita conflito de event loop)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    configure_logging()
    settings.ensure_directories()

    # Migrations: Alembic em produção, create_all em testes
    # asyncio.to_thread evita o RuntimeError "event loop already running"
    # pois Alembic chama asyncio.run() internamente
    if settings.skip_migrations:
        await create_tables()
        logger.info("Tabelas criadas via ORM (modo teste)")
    else:
        try:
            import asyncio
            await asyncio.to_thread(_run_migrations)
            logger.info("Migrations Alembic aplicadas")
        except Exception as exc:
            logger.warning("Alembic falhou ({}). Fallback: create_all", exc)
            await create_tables()

    await init_db()

    # Scheduler: só inicia se RUN_SCHEDULER=true (evita jobs duplicados em multi-worker)
    scheduler = None
    if settings.run_scheduler:
        scheduler = create_scheduler()
        scheduler.start()
        logger.info("Scheduler iniciado — weekly prompt toda segunda 09:00 BRT")

    yield

    if scheduler:
        scheduler.shutdown()
        logger.info("Scheduler encerrado")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        openapi_url="/openapi.json" if settings.debug else None,
        lifespan=lifespan,
    )

    app.add_middleware(RateLimitMiddleware, requests_per_minute=120)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["POST", "GET"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(webhook.router)
    app.include_router(mercadopago.router)
    app.include_router(admin.router)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )
