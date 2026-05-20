"""
app/integrations/groq/client.py
Groq Vision client for OCR on SPX Driver screenshots.
"""
import base64
import json
import re
from pathlib import Path

from groq import Groq
from loguru import logger

from app.core.config import settings
from app.core.exceptions import OcrExtractionError

_SYSTEM_PROMPT = (
    "You are an OCR assistant specialized in Brazilian delivery app screenshots. "
    "Extract earnings data precisely and return only valid JSON."
)

_USER_PROMPT = (
    "Analyze this SPX Driver (Shopee) screenshot and extract the weekly earnings total "
    "and the period. Respond ONLY with a JSON object, no extra text:\n"
    '{"valor": 350.00, "periodo": "12/05 a 18/05/2025"}'
)


class GroqVisionClient:
    def __init__(self) -> None:
        self._client = Groq(api_key=settings.groq_api_key)
        self._model = settings.groq_model

    def extract_earnings(self, image_path: Path) -> dict:
        if not image_path.exists():
            raise OcrExtractionError(f"Image not found: {image_path}")

        mime = "image/jpeg" if image_path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                            {"type": "text", "text": _USER_PROMPT},
                        ],
                    },
                ],
                max_tokens=200,
                temperature=0,
            )
        except Exception as exc:
            raise OcrExtractionError(f"Groq API error: {exc}") from exc

        raw = response.choices[0].message.content or ""
        logger.debug("Groq raw response: {}", raw[:300])

        cleaned = re.sub(r"```json|```", "", raw).strip()
        match = re.search(r"\{.*?\}", cleaned, re.DOTALL)
        if not match:
            raise OcrExtractionError(f"No JSON found in response: {raw[:200]}")

        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise OcrExtractionError(f"Invalid JSON from model: {exc}") from exc

        value = data.get("valor")
        if value is None:
            raise OcrExtractionError("Field 'valor' missing in model response")

        return {
            "valor": float(value),
            "periodo": data.get("periodo", ""),
        }


groq_client = GroqVisionClient()
