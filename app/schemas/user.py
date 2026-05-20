"""
app/schemas/user.py
"""
from datetime import datetime

from pydantic import BaseModel


class UserResponse(BaseModel):
    id: int
    whatsapp_number: str
    name: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
