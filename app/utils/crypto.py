"""
app/utils/crypto.py
Fernet symmetric encryption for passwords stored at rest.
Key is read from ENCRYPTION_KEY in .env; auto-generated and persisted if absent.
"""
from pathlib import Path

from cryptography.fernet import Fernet
from loguru import logger


def _resolve_key() -> bytes:
    from app.core.config import settings  # deferred to avoid circular import at module load

    key = settings.encryption_key.strip()
    if key:
        return key.encode()

    # Auto-generate and write back to .env so the key survives restarts.
    new_key = Fernet.generate_key()
    env_path = Path(".env")
    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
        if "ENCRYPTION_KEY=" in content:
            content = content.replace("ENCRYPTION_KEY=", f"ENCRYPTION_KEY={new_key.decode()}")
        else:
            content += f"\nENCRYPTION_KEY={new_key.decode()}\n"
        env_path.write_text(content, encoding="utf-8")

    logger.warning(
        "ENCRYPTION_KEY nao configurada — chave Fernet gerada automaticamente e salva em .env. "
        "Nao altere ou perca essa chave: senhas criptografadas no banco ficam ilegíveis sem ela."
    )
    return new_key


_KEY: bytes = _resolve_key()


def encrypt(text: str) -> str:
    return Fernet(_KEY).encrypt(text.encode()).decode()


def decrypt(text: str) -> str:
    return Fernet(_KEY).decrypt(text.encode()).decode()
