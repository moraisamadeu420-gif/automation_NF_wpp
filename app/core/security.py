"""
app/core/security.py
Internal authentication helpers.
"""
import hashlib
import hmac

from app.core.config import settings


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """Verify Evolution API webhook HMAC-SHA256 signature."""
    if not settings.webhook_secret:
        return True

    expected = hmac.new(
        settings.webhook_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


def verify_internal_api_key(api_key: str) -> bool:
    """Verify requests from internal services."""
    if not settings.internal_api_key:
        return True
    return hmac.compare_digest(settings.internal_api_key, api_key)
