"""
app/api/dependencies.py
FastAPI dependency injection — database session and auth guards.
"""
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_internal_api_key, verify_webhook_signature
from app.database.session import get_session

DbSession = Annotated[AsyncSession, Depends(get_session)]


async def require_webhook_auth(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
) -> None:
    body = await request.body()
    sig = x_hub_signature_256 or ""
    if not verify_webhook_signature(body, sig):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")


async def require_internal_auth(
    x_api_key: str | None = Header(default=None),
) -> None:
    if not verify_internal_api_key(x_api_key or ""):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
