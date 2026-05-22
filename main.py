"""
main.py
FastAPI application factory and entry point.

Run with:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
Or via Makefile:
    make dev
"""
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.api.middleware import RateLimitMiddleware, RequestLoggingMiddleware
from app.api.routes import health, mercadopago, webhook
from app.core.config import settings
from app.core.logging import configure_logging
from app.database.connection import create_tables, upgrade_schema
from app.workers.scheduler import create_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    configure_logging()
    settings.ensure_directories()
    await create_tables()
    await upgrade_schema()

    scheduler = create_scheduler()
    scheduler.start()
    logger.info("Scheduler started — weekly NFSe prompt every Monday at 09:00 BRT")

    yield

    scheduler.shutdown()
    logger.info("Scheduler stopped")


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
