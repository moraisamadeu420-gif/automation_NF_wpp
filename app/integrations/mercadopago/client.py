"""
app/integrations/mercadopago/client.py
Mercado Pago payment integration (synchronous SDK wrapped for async use).
"""
import mercadopago
from loguru import logger

from app.core.config import settings


class MercadoPagoClient:
    def _sdk(self) -> mercadopago.SDK:
        return mercadopago.SDK(settings.mercadopago_access_token)

    def create_preference(self, user_id: int, whatsapp_number: str) -> str:
        """Creates a one-time checkout preference. Returns the init_point URL."""
        if not settings.mercadopago_access_token:
            raise RuntimeError("MERCADOPAGO_ACCESS_TOKEN not configured")

        preference_data: dict = {
            "items": [{
                "title": "Acesso Bot NFSe — 30 dias",
                "quantity": 1,
                "unit_price": float(settings.subscription_price),
                "currency_id": "BRL",
            }],
            "external_reference": str(user_id),
            "metadata": {"user_id": user_id, "whatsapp_number": whatsapp_number},
        }
        if settings.bot_public_url:
            preference_data["notification_url"] = (
                f"{settings.bot_public_url.rstrip('/')}/webhook/mercadopago"
            )

        result = self._sdk().preference().create(preference_data)
        if result["status"] not in (200, 201):
            raise RuntimeError(f"MP preference creation failed: {result}")
        url = result["response"]["init_point"]
        logger.info("MP one-time preference created for user {} → {}", user_id, url)
        return url

    def get_payment(self, payment_id: str) -> dict:
        """Returns full payment data for the given payment ID."""
        if not settings.mercadopago_access_token:
            raise RuntimeError("MERCADOPAGO_ACCESS_TOKEN not configured")
        result = self._sdk().payment().get(payment_id)
        if result["status"] != 200:
            raise RuntimeError(f"MP payment.get failed: {result}")
        return result["response"]


mp_client = MercadoPagoClient()
