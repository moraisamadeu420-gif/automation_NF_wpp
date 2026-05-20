"""
app/api/routes/health.py
Liveness and readiness probes.
"""
from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import settings
from app.integrations.evolution.client import evolution_client

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    whatsapp_connected: bool


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    connected = await evolution_client.check_connection()
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        whatsapp_connected=connected,
    )


@router.get("/", include_in_schema=False)
async def root() -> dict:
    return {"service": settings.app_name, "version": settings.app_version}
