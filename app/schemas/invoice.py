"""
app/schemas/invoice.py
"""
from datetime import datetime

from pydantic import BaseModel


class InvoiceResponse(BaseModel):
    id: int
    user_id: int
    invoice_number: str | None
    value: float
    period: str | None
    municipality: str | None
    status: str
    pdf_path: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
