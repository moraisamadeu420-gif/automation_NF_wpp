"""
app/adapters/base_adapter.py
Abstract interface that every municipality adapter must implement.
All Playwright automation runs synchronously inside a thread executor.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EmissionRequest:
    value: float
    period: str
    username: str
    password: str
    portal_url: str
    prestador_nome: str
    prestador_cnpj: str
    tomador_cnpj: str
    tomador_razao_social: str
    service_description: str
    aliquota_iss: float = 2.0
    headless: bool = True
    slow_mo: int = 300


@dataclass
class EmissionResult:
    invoice_number: str | None
    pdf_path: Path | None
    xml_path: Path | None


class BaseNfseAdapter(ABC):
    """
    Each concrete adapter handles one municipality's NFSe portal.
    Adapters are stateless; all context is passed via EmissionRequest.
    """

    @property
    @abstractmethod
    def municipality_key(self) -> str:
        """Unique identifier for this municipality (e.g. 'campinas', 'nacional')."""

    @abstractmethod
    def emit(self, request: EmissionRequest) -> EmissionResult:
        """
        Synchronous emission flow. Runs inside asyncio thread executor.
        Must raise NfseEmissionError on failure.
        """
