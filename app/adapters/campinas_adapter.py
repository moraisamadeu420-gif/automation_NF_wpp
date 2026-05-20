"""
app/adapters/campinas_adapter.py
Adapter for the Campinas municipal NFSe portal.
Stub — implement the specifics of campinas.nfse.com.br when ready.
"""
from loguru import logger

from app.adapters.base_adapter import BaseNfseAdapter, EmissionRequest, EmissionResult
from app.core.exceptions import NfseEmissionError


class CampinasAdapter(BaseNfseAdapter):
    @property
    def municipality_key(self) -> str:
        return "campinas"

    def emit(self, request: EmissionRequest) -> EmissionResult:
        # TODO: implement Playwright automation for Campinas portal
        raise NfseEmissionError("Campinas adapter not yet implemented", stage="init")
