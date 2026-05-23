"""
app/adapters/nacional_adapter.py
Adapter para o portal Emissor Nacional (nfse.gov.br).
Seletores capturados via playwright codegen na emissão completa.
"""
import re
from datetime import date, datetime
from pathlib import Path

from loguru import logger
from playwright.sync_api import Page, sync_playwright

from app.adapters.base_adapter import BaseNfseAdapter, EmissionRequest, EmissionResult
from app.core.config import settings
from app.core.exceptions import NfseEmissionError


def _screenshot(page: Page, label: str) -> str | None:
    try:
        path = settings.screenshots_path / f"{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        page.screenshot(path=str(path))
        logger.info("Screenshot erro: {}", path.name)
        return str(path)
    except Exception:
        return None


_PORTAL_URL   = "https://www.nfse.gov.br/EmissorNacional/Login"
_TOMADOR_CNPJ = "42.446.277/0001-92"


class NacionalAdapter(BaseNfseAdapter):

    @property
    def municipality_key(self) -> str:
        return "nacional"

    def emit(self, request: EmissionRequest) -> EmissionResult:
        if settings.dry_run or settings.nfse_dry_run:
            logger.info("[DRY RUN] Simulando emissão — Playwright não será executado")
            return EmissionResult(invoice_number="DRY-RUN-001", pdf_path=None, xml_path=None)

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
                self._abrir_emissao_completa(page)
                self._preencher_data_e_tomador(page, request)
                self._preencher_municipio_servico_descricao(page, request)
                self._preencher_valor(page, request)
                self._emitir(page)
                numero = self._extrair_numero_nota(page)
                pdf_path = self._baixar_pdf(page, request)
                return EmissionResult(invoice_number=numero, pdf_path=pdf_path, xml_path=None)
            except NfseEmissionError:
                raise
            except Exception as exc:
                shot = _screenshot(page, "erro_geral")
                raise NfseEmissionError(str(exc), stage="unknown", screenshot_path=shot) from exc
            finally:
                context.close()
                browser.close()

    # ── 1. login ──────────────────────────────────────────────────────────────

    def _login(self, page: Page, request: EmissionRequest) -> None:
        logger.info("Fazendo login...")
        try:
            page.goto(_PORTAL_URL, wait_until="networkidle", timeout=60_000)

            page.get_by_role("textbox", name="CPF/CNPJ").click()
            page.get_by_role("textbox", name="CPF/CNPJ").fill(request.username)
            page.get_by_role("textbox", name="Senha").click()
            page.get_by_role("textbox", name="Senha").fill(request.password)
            page.get_by_role("button", name="Entrar").click()

            page.wait_for_load_state("networkidle", timeout=30_000)

            if "login" in page.url.lower():
                shot = _screenshot(page, "login_falhou")
                raise NfseEmissionError(
                    "Credenciais invalidas ou sessao expirada. Verifique usuario e senha.",
                    stage="login",
                    critical=True,
                    screenshot_path=shot,
                )
            logger.info("Login OK — URL: {}", page.url)
        except NfseEmissionError:
            raise
        except Exception as exc:
            shot = _screenshot(page, "login_erro")
            raise NfseEmissionError(f"Falha no login: {exc}", stage="login", critical=True, screenshot_path=shot) from exc

    # ── 2. abrir emissão completa ─────────────────────────────────────────────

    def _abrir_emissao_completa(self, page: Page) -> None:
        logger.info("Abrindo emissão completa...")
        try:
            page.get_by_role("button").filter(has_text="Nova NFS-e").click(timeout=60_000)
            page.get_by_role("link", name="Emissão completa").click(timeout=60_000)
            page.wait_for_load_state("domcontentloaded")
            logger.info("Formulário aberto")
        except NfseEmissionError:
            raise
        except Exception as exc:
            shot = _screenshot(page, "nav_erro")
            raise NfseEmissionError(f"Falha ao abrir emissão: {exc}", stage="navigation", screenshot_path=shot) from exc

    # ── 3+4+5. data de competência + tomador + Avançar ────────────────────────

    def _preencher_data_e_tomador(self, page: Page, request: EmissionRequest) -> None:
        logger.info("Preenchendo data e tomador...")
        try:
            # Data de competência — usa CSS .day para evitar ambiguidade do get_by_role
            dia_atual = date.today().day
            page.locator("#btn_DataCompetencia").click(timeout=60_000)
            page.wait_for_selector("td.day", timeout=10_000)
            day_cells = page.locator("td.day")
            clicked = False
            for i in range(day_cells.count()):
                cell = day_cells.nth(i)
                classes = cell.get_attribute("class") or ""
                text = (cell.text_content() or "").strip()
                if text == str(dia_atual) and "disabled" not in classes and "old" not in classes and "new" not in classes:
                    cell.click(timeout=10_000)
                    clicked = True
                    break
            if not clicked:
                # fallback: clica no primeiro .day que bate o número (ignora classes)
                page.locator("td.day").filter(has_text=re.compile(f"^{dia_atual}$")).first.click(timeout=30_000)

            # Tomador — Pessoa Jurídica + CNPJ Shopee (fixo)
            page.locator(
                ".form-group.form-group-lg > .radio-options > div:nth-child(2) > label > .cr > .cr-icon"
            ).first.click(timeout=60_000)
            page.locator("#Tomador_Inscricao").click(timeout=60_000)
            page.locator("#Tomador_Inscricao").fill(_TOMADOR_CNPJ)
            page.locator("#btn_Tomador_Inscricao_pesquisar").click(timeout=60_000)
            page.wait_for_load_state("networkidle", timeout=30_000)

            # Avança para a etapa de município/serviço
            page.get_by_role("button", name="Avançar").click(timeout=60_000)
            page.wait_for_load_state("domcontentloaded")

            logger.info("Data dia {} | Tomador {} preenchidos", dia_atual, _TOMADOR_CNPJ)
        except NfseEmissionError:
            raise
        except Exception as exc:
            shot = _screenshot(page, "data_tomador_erro")
            raise NfseEmissionError(f"Falha na data/tomador: {exc}", stage="dados_gerais", critical=True, screenshot_path=shot) from exc

    # ── 6+7+8. município + serviço + descrição + Avançar ─────────────────────

    def _preencher_municipio_servico_descricao(self, page: Page, request: EmissionRequest) -> None:
        logger.info("Preenchendo município, serviço e descrição...")
        try:
            # Município — preserva o estado informado pelo usuário (ex: "Campinas/SP")
            parts = request.municipio.split("/")
            municipio_cidade = parts[0].strip()
            municipio_estado = parts[1].strip() if len(parts) > 1 else "SP"
            page.locator("#pnlLocalPrestacao").get_by_label("").click(timeout=60_000)
            page.get_by_role("searchbox", name="Search").fill(municipio_cidade)
            page.get_by_role("option", name=f"{municipio_cidade}/{municipio_estado}").click(timeout=60_000)

            # Código do serviço — digita e pressiona Enter
            page.get_by_label("", exact=True).click(timeout=60_000)
            page.get_by_role("searchbox", name="Search").fill("160201")
            page.get_by_role("searchbox", name="Search").press("Enter")

            # Descrição
            descricao = (
                f"Nota referente aos serviços de entregas prestados "
                f"no período de {request.period}."
            )
            page.locator("i").nth(1).click(timeout=60_000)
            page.locator("#ServicoPrestado_Descricao").click(timeout=60_000)
            page.locator("#ServicoPrestado_Descricao").fill(descricao)

            page.get_by_role("button", name="Avançar").click(timeout=60_000)
            page.wait_for_load_state("domcontentloaded")

            logger.info("Município {}/{} | Serviço 160201 | Descrição preenchida", municipio_cidade, municipio_estado)
        except NfseEmissionError:
            raise
        except Exception as exc:
            shot = _screenshot(page, "municipio_servico_erro")
            raise NfseEmissionError(
                f"Falha no município/serviço/descrição: {exc}", stage="servico", screenshot_path=shot
            ) from exc

    # ── 9. valor + Avançar ────────────────────────────────────────────────────

    def _preencher_valor(self, page: Page, request: EmissionRequest) -> None:
        logger.info("Preenchendo valor R$ {:.2f}...", request.value)
        try:
            valor_fmt = f"{request.value:.2f}".replace(".", ",")
            page.locator("#Valores_ValorServico").click(timeout=60_000)
            page.locator("#Valores_ValorServico").fill(valor_fmt)
            page.get_by_role("button", name="Avançar").click(timeout=60_000)
            page.wait_for_load_state("domcontentloaded")
            logger.info("Valor {} preenchido", valor_fmt)
        except NfseEmissionError:
            raise
        except Exception as exc:
            shot = _screenshot(page, "valor_erro")
            raise NfseEmissionError(
                f"Falha ao preencher valor: {exc}", stage="tomador_valor", critical=True, screenshot_path=shot
            ) from exc

    # ── 10. emitir ────────────────────────────────────────────────────────────

    def _emitir(self, page: Page) -> None:
        logger.info("Emitindo NFS-e...")
        try:
            page.locator("#btnProsseguir").click(timeout=60_000)
            page.wait_for_load_state("networkidle", timeout=60_000)
            logger.info("NFS-e emitida")
        except NfseEmissionError:
            raise
        except Exception as exc:
            shot = _screenshot(page, "emissao_erro")
            raise NfseEmissionError(f"Falha ao emitir: {exc}", stage="submit", screenshot_path=shot) from exc

    # ── número da nota ────────────────────────────────────────────────────────

    def _extrair_numero_nota(self, page: Page) -> str | None:
        for selector in [
            "[id*='NumeroNfse']",
            "td:has-text('Número')",
            "strong:has-text('Número')",
            "span[id*='numero']",
        ]:
            try:
                el = page.locator(selector).first
                if el.is_visible():
                    nums = re.findall(r"\d+", el.text_content() or "")
                    if nums:
                        logger.info("Número da nota: {}", nums[0])
                        return nums[0]
            except Exception:
                continue
        logger.warning("Número da nota não encontrado")
        return None

    # ── 11. baixar PDF ────────────────────────────────────────────────────────

    def _baixar_pdf(self, page: Page, request: EmissionRequest) -> Path | None:
        logger.info("Baixando PDF...")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = settings.pdf_path / f"{request.user_id}_{timestamp}.pdf"
        try:
            with page.expect_download(timeout=30_000) as download_info:
                page.get_by_role("link", name="Baixar DANFSe").click(timeout=60_000)
            download_info.value.save_as(str(dest))
            logger.info("PDF salvo: {}", dest.name)
            return dest
        except Exception as exc:
            _screenshot(page, "pdf_erro")
            logger.warning("Falha ao baixar PDF: {}", exc)
            return None
