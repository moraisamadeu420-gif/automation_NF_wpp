"""
app/integrations/evolution/client.py
Async HTTP client for Evolution API (WhatsApp).
"""
import base64
from pathlib import Path

import httpx
from loguru import logger

from app.core.config import settings
from app.core.exceptions import EvolutionApiError


class EvolutionClient:
    def __init__(self) -> None:
        self._base_url = settings.evolution_url.rstrip("/")
        self._api_key = settings.evolution_api_key
        self._instance = settings.evolution_instance

    @property
    def _headers(self) -> dict[str, str]:
        return {"apikey": self._api_key, "Content-Type": "application/json"}

    @property
    def _dry_run(self) -> bool:
        return settings.dry_run

    async def send_text(self, number: str, text: str) -> None:
        if self._dry_run:
            logger.info("[DRY RUN] send_text → {} | {}", number, text)
            return
        url = f"{self._base_url}/message/sendText/{self._instance}"
        await self._post(url, {"number": number, "text": text})

    async def send_pdf(self, number: str, pdf_path: Path, caption: str = "") -> None:
        if self._dry_run:
            logger.info("[DRY RUN] send_pdf → {} | file: {} | caption: {}", number, pdf_path.name, caption)
            return
        with open(pdf_path, "rb") as f:
            pdf_b64 = base64.b64encode(f.read()).decode()
        url = f"{self._base_url}/message/sendMedia/{self._instance}"
        await self._post(url, {
            "number": number,
            "mediatype": "document",
            "mimetype": "application/pdf",
            "caption": caption,
            "media": pdf_b64,
            "fileName": pdf_path.name,
        })

    async def send_image(self, number: str, image_path: Path, caption: str = "") -> None:
        if self._dry_run:
            logger.info("[DRY RUN] send_image → {} | file: {} | caption: {}", number, image_path.name, caption)
            return
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        url = f"{self._base_url}/message/sendMedia/{self._instance}"
        await self._post(url, {
            "number": number,
            "mediatype": "image",
            "mimetype": "image/jpeg",
            "caption": caption,
            "media": img_b64,
            "fileName": image_path.name,
        })

    async def send_video(self, number: str, video_url: str, caption: str = "") -> None:
        """Envia vídeo MP4 a partir de uma URL pública (Google Drive, CDN, etc.)."""
        if self._dry_run:
            logger.info("[DRY RUN] send_video → {} | url: {} | caption: {}", number, video_url, caption)
            return
        url = f"{self._base_url}/message/sendMedia/{self._instance}"
        await self._post(url, {
            "number": number,
            "mediatype": "video",
            "mimetype": "video/mp4",
            "caption": caption,
            "media": video_url,
        })

    async def download_media(self, message_id: str) -> bytes:
        if self._dry_run:
            logger.info("[DRY RUN] download_media → id: {}", message_id)
            raise EvolutionApiError("download_media unavailable in DRY_RUN mode", status_code=0)
        url = f"{self._base_url}/chat/getBase64FromMediaMessage/{self._instance}"
        payload = {"message": {"key": {"id": message_id}}}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=self._headers, json=payload)
        if response.status_code not in (200, 201):
            raise EvolutionApiError("Failed to download media", status_code=response.status_code)
        data = response.json()
        b64 = data.get("base64", "")
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        return base64.b64decode(b64)

    async def delete_message(self, remote_jid: str, msg_id: str) -> None:
        """Tenta deletar mensagem via Evolution API. Silencioso em erros.
        Nota: WhatsApp não permite deletar mensagens recebidas de terceiros —
        esta chamada funciona apenas para mensagens enviadas pelo próprio bot."""
        if self._dry_run:
            logger.info("[DRY RUN] delete_message → jid: {} | id: {}", remote_jid, msg_id)
            return
        url = f"{self._base_url}/chat/deleteMessage/{self._instance}"
        payload = {"id": msg_id, "fromMe": False, "remoteJid": remote_jid}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.delete(url, headers=self._headers, json=payload)
            logger.debug("delete_message → status: {} | body: {}", response.status_code, response.text[:120])
        except Exception as exc:
            logger.debug("delete_message falhou (ignorado): {}", exc)

    async def check_connection(self) -> bool:
        if self._dry_run:
            return True
        url = f"{self._base_url}/instance/connectionState/{self._instance}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url, headers=self._headers)
            state = response.json().get("instance", {}).get("state", "")
            return state == "open"
        except Exception:
            return False

    async def _post(self, url: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=self._headers, json=payload)
        if response.status_code not in (200, 201):
            logger.warning("Evolution API error {} — {}", response.status_code, response.text[:200])
            raise EvolutionApiError(response.text, status_code=response.status_code)
        return response.json()


evolution_client = EvolutionClient()
