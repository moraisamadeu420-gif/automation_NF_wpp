"""
app/schemas/webhook.py
FastAPI request/response schemas for webhook endpoints.
"""
from typing import Any

from pydantic import BaseModel


class WebhookPayload(BaseModel):
    event: str
    instance: str
    data: dict[str, Any]


class WebhookResponse(BaseModel):
    status: str = "ok"
