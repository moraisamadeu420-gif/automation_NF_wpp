"""
Import all models so that Alembic can detect them for autogenerate.
"""
from app.models.credential import NfseCredential
from app.models.invoice import Invoice, InvoiceStatus
from app.models.session import ConversationState, WhatsappSession
from app.models.user import User

__all__ = [
    "User",
    "NfseCredential",
    "Invoice",
    "InvoiceStatus",
    "WhatsappSession",
    "ConversationState",
]
