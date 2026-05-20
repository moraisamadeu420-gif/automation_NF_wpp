"""
app/services/ocr_service.py
Orchestrates OCR extraction — currently uses Groq Vision.
Isolating this makes it trivial to swap in Tesseract, Azure, etc.
"""
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.integrations.groq.client import groq_client


class OcrService:
    async def extract_earnings_from_image(self, image_path: Path) -> dict:
        """
        Returns: {"valor": float, "periodo": str, "data_leitura": str}
        Raises OcrExtractionError on failure.
        """
        logger.info("Running OCR on image: {}", image_path.name)
        data = groq_client.extract_earnings(image_path)
        data["data_leitura"] = datetime.now().isoformat()
        if not data.get("periodo"):
            data["periodo"] = datetime.now().strftime("semana de %d/%m/%Y")
        logger.info("OCR result — value: R$ {:.2f} | period: {}", data["valor"], data["periodo"])
        return data


ocr_service = OcrService()
