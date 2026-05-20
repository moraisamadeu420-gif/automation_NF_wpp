"""
app/adapters/nacional_adapter.py
Adapter for the Emissor Nacional NFSe portal (nfse.gov.br).
Ported and refactored from the original modules/nfse_web.py.
"""
import random
import time
from datetime import datetime
from pathlib import Path

from loguru import logger
from playwright.sync_api import Browser, Page, TimeoutError as PWTimeoutError, sync_playwright

from app.adapters.base_adapter import BaseNfseAdapter, EmissionRequest, EmissionResult
from app.core.config import settings
from app.core.exceptions import NfseEmissionError


def _sleep(min_s: float, max_s: float) -> None:
    time.sleep(random.uniform(min_s, max_s))


def _short() -> None:
    _sleep(0.8, 2.0)


def _medium() -> None:
    _sleep(2.0, 4.0)


def _long() -> None:
    _sleep(4.0, 7.0)


def _screenshot(page: Page, label: str) -> None:
    try:
        path = settings.screenshots_path / f"{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        page.screenshot(path=str(path))
    except Exception:
        pass


class NacionalAdapter(BaseNfseAdapter):
    """Handles login and emission on the Emissor Nacional portal."""

    @property
    def municipality_key(self) -> str:
        return "nacional"

    def emit(self, request: EmissionRequest) -> EmissionResult:
        settings.ensure_directories()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=request.headless,
                slow_mo=request.slow_mo,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                accept_downloads=True,
            )
            page = context.new_page()
            try:
                self._login(page, request)
                _medium()
                self._navigate_to_emission(page)
                _medium()
                self._fill_form(page, request)
                _medium()
                self._submit(page)
                _medium()
                invoice_number = self._extract_invoice_number(page)
                pdf_path = self._download_pdf(page, invoice_number)
                return EmissionResult(
                    invoice_number=invoice_number,
                    pdf_path=pdf_path,
                    xml_path=None,
                )
            except NfseEmissionError:
                raise
            except Exception as exc:
                _screenshot(page, "erro_geral")
                raise NfseEmissionError(str(exc), stage="unknown") from exc
            finally:
                context.close()
                browser.close()

    def _login(self, page: Page, request: EmissionRequest) -> None:
        logger.info("Performing login on Emissor Nacional portal")
        try:
            page.goto(request.portal_url, wait_until="networkidle", timeout=30_000)
            _medium()
            page.get_by_role("textbox", name="CPF/CNPJ").fill(request.username)
            _short()
            page.get_by_role("textbox", name="Senha").fill(request.password)
            _short()
            page.get_by_role("button", name="Entrar").click()
            page.wait_for_load_state("networkidle", timeout=20_000)
            _medium()
            logger.info("Login successful")
        except Exception as exc:
            _screenshot(page, "login_erro")
            raise NfseEmissionError(f"Login failed: {exc}", stage="login") from exc

    def _navigate_to_emission(self, page: Page) -> None:
        logger.info("Navigating to simplified emission screen")
        try:
            page.get_by_role("button").filter(has_text="Nova NFS-e").click()
            _medium()
            page.get_by_role("link", name="Emissão simplificada").click()
            page.wait_for_load_state("networkidle", timeout=15_000)
            _medium()
        except Exception as exc:
            _screenshot(page, "nav_erro")
            raise NfseEmissionError(f"Navigation failed: {exc}", stage="navigation") from exc

    def _fill_form(self, page: Page, request: EmissionRequest) -> None:
        logger.info("Filling emission form — value: R$ {:.2f}", request.value)
        try:
            page.locator("#InscricaoCliente").fill(request.tomador_cnpj)
            _short()
            page.locator("#btn_InscricaoCliente_pesquisar").click()
            _medium()

            page.locator("#IdServicoFavorito_chosen a").filter(has_text="Selecione...").click()
            _short()
            page.locator("#IdServicoFavorito_chosen").get_by_text(request.service_favorite_name).click()
            _short()

            page.locator("#btnDescricao").click()
            _medium()
            page.locator("#Descricao").fill(request.service_description)
            _short()

            value_str = f"{request.value:.2f}".replace(".", ",")
            page.locator("#ValorServico").fill(value_str)
            _short()

            page.locator("#InfoComplementar_CodigoMunicipio_chosen a").filter(has_text="Selecione...").click()
            _short()
            page.locator("#InfoComplementar_CodigoMunicipio_chosen").get_by_role("textbox").fill("campinas")
            _short()
            page.locator("#InfoComplementar_CodigoMunicipio_chosen").get_by_text("Campinas/SP").click()
            _medium()

            logger.info("Form filled successfully")
        except Exception as exc:
            _screenshot(page, "form_erro")
            raise NfseEmissionError(f"Form fill failed: {exc}", stage="form") from exc

    def _submit(self, page: Page) -> None:
        logger.info("Submitting emission")
        try:
            page.get_by_role("button", name="Emitir").click()
            page.wait_for_load_state("networkidle", timeout=30_000)
            _long()
        except Exception as exc:
            _screenshot(page, "submit_erro")
            raise NfseEmissionError(f"Submission failed: {exc}", stage="submit") from exc

    def _extract_invoice_number(self, page: Page) -> str | None:
        import re

        for selector in ["td:has-text('Número')", "span[id*='numero']", "strong:has-text('Número')"]:
            try:
                el = page.locator(selector).first
                if el.is_visible():
                    numbers = re.findall(r"\d+", el.text_content() or "")
                    if numbers:
                        return numbers[0]
            except Exception:
                continue
        return None

    def _download_pdf(self, page: Page, invoice_number: str | None) -> Path | None:
        logger.info("Downloading invoice PDF")
        suffix = invoice_number or datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = settings.pdf_path / f"nfse_{suffix}.pdf"
        try:
            with page.expect_download(timeout=30_000) as dl:
                page.locator("a[href*='DANFSe']").first.click()
            dl.value.save_as(str(dest))
            logger.info("PDF saved: {}", dest.name)
            return dest
        except Exception as exc:
            _screenshot(page, "download_erro")
            logger.warning("PDF download failed: {}", exc)
            return None
