"""
app/api/routes/health.py
Liveness e readiness probes.

GET /health  — usado pelo Docker HEALTHCHECK e Railway health probe
GET /         — raiz
"""
from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import text

from app.core.config import settings
from app.database.connection import engine
from app.integrations.evolution.client import evolution_client

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str        # "ok" | "degraded"
    version: str
    database_ok: bool
    whatsapp_connected: bool


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    # Ping no banco — detecta conexão morta antes do Railway reiniciar o container
    db_ok = False
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:
        logger.error("Health check: DB ping falhou — {}", exc)

    connected = await evolution_client.check_connection()

    return HealthResponse(
        status="ok" if db_ok else "degraded",
        version=settings.app_version,
        database_ok=db_ok,
        whatsapp_connected=connected,
    )


@router.get("/", include_in_schema=False)
async def root() -> dict:
    return {"service": settings.app_name, "version": settings.app_version}
