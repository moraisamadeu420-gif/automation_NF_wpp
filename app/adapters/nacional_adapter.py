"""
app/adapters/nacional_adapter.py
Adapter for the Emissor Nacional NFSe portal (nfse.gov.br).
Uses the full emission form (Emissão Completa) — no "serviço favorito" required.
Works for any SPX Driver courier without prior portal configuration.
"""
import random
import re
import time
from datetime import datetime
from pathlib import Path

from loguru import logger
from playwright.sync_api import Page, sync_playwright

from app.adapters.base_adapter import BaseNfseAdapter, EmissionRequest, EmissionResult
from app.core.config import settings
from app.core.exceptions import NfseEmissionError

# ── timing helpers ────────────────────────────────────────────────────────────

def _sleep(min_s: float, max_s: float) -> None:
    time.sleep(random.uniform(min_s, max_s))

def _curto() -> None:  _sleep(0.8, 1.5)
def _medio() -> None:  _sleep(2.0, 3.5)
def _longo() -> None:  _sleep(4.0, 7.0)


def _screenshot(page: Page, label: str) -> None:
    try:
        path = settings.screenshots_path / f"{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        page.screenshot(path=str(path))
        logger.info("Screenshot: {}", path.name)
    except Exception:
        pass


# ── constants (SPX Driver / Shopee) ──────────────────────────────────────────

_TOMADOR_CNPJ = "42.446.277/0001-92"

# Item da lista LC 116/2003 para entrega/logística — ajuste se o portal rejeitar
# 17.09 = Transporte de natureza municipal
# 26.01 = Coleta, remessa ou entrega de correspondências/objetos/bens
_ITEM_LISTA_SERVICO = "17.09"

_MUNICIPIO_PRESTACAO = "Campinas"
_MUNICIPIO_OPCAO_TEXTO = "Campinas/SP"


# ── adapter ───────────────────────────────────────────────────────────────────

class NacionalAdapter(BaseNfseAdapter):
    """Automação do portal Emissor Nacional (nfse.gov.br) — formulário completo."""

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
                _medio()
                self._navegar_para_emissao_completa(page)
                _medio()
                self._preencher_tomador(page)
                _medio()
                self._preencher_servico(page, request)
                _medio()
                self._preencher_valores(page, request)
                _medio()
                self._emitir(page)
                _medio()
                numero = self._extrair_numero_nota(page)
                pdf_path = self._baixar_pdf(page, numero)
                return EmissionResult(
                    invoice_number=numero,
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

    # ── login ─────────────────────────────────────────────────────────────────

    def _login(self, page: Page, request: EmissionRequest) -> None:
        logger.info("Login no portal Emissor Nacional...")
        try:
            page.goto(request.portal_url, wait_until="networkidle", timeout=30_000)
            _medio()

            campo_usuario = page.get_by_role("textbox", name="CPF/CNPJ")
            campo_usuario.click()
            _curto()
            campo_usuario.fill(request.username)
            _curto()

            campo_senha = page.get_by_role("textbox", name="Senha")
            campo_senha.click()
            _curto()
            campo_senha.fill(request.password)
            _curto()

            page.get_by_role("button", name="Entrar").click()
            page.wait_for_load_state("networkidle", timeout=20_000)
            _medio()
            logger.info("Login realizado")
        except NfseEmissionError:
            raise
        except Exception as exc:
            _screenshot(page, "login_erro")
            raise NfseEmissionError(f"Falha no login: {exc}", stage="login") from exc

    # ── navegação ─────────────────────────────────────────────────────────────

    def _navegar_para_emissao_completa(self, page: Page) -> None:
        logger.info("Navegando para emissão completa...")
        try:
            page.get_by_role("button").filter(has_text="Nova NFS-e").click(timeout=60_000)
            _medio()
            _screenshot(page, "menu_nova_nfse")

            # Emissão completa em vez de simplificada
            page.get_by_role("link", name="Emissão completa").click()
            page.wait_for_load_state("networkidle", timeout=15_000)
            _medio()
            _screenshot(page, "form_emissao_completa")
            logger.info("Formulário de emissão completa aberto")
        except NfseEmissionError:
            raise
        except Exception as exc:
            _screenshot(page, "nav_erro")
            raise NfseEmissionError(f"Falha na navegação: {exc}", stage="navigation") from exc

    # ── tomador ───────────────────────────────────────────────────────────────

    def _preencher_tomador(self, page: Page) -> None:
        logger.info("Preenchendo tomador (Shopee)...")
        try:
            campo_cnpj = page.locator("#InscricaoCliente")
            campo_cnpj.click()
            _curto()
            campo_cnpj.fill(_TOMADOR_CNPJ)
            _curto()
            page.locator("#btn_InscricaoCliente_pesquisar").click()
            _medio()
            _screenshot(page, "tomador_preenchido")
            logger.info("Tomador preenchido")
        except NfseEmissionError:
            raise
        except Exception as exc:
            _screenshot(page, "tomador_erro")
            raise NfseEmissionError(f"Falha ao preencher tomador: {exc}", stage="tomador") from exc

    # ── serviço ───────────────────────────────────────────────────────────────

    def _preencher_servico(self, page: Page, request: EmissionRequest) -> None:
        logger.info("Preenchendo dados do serviço...")
        try:
            # Item da lista LC 116
            # O campo pode ser um chosen ou um input direto — tenta chosen primeiro
            item_chosen = page.locator("[id*='ItemListaServico_chosen'], [id*='ListaServico_chosen']").first
            if item_chosen.is_visible(timeout=3_000):
                item_chosen.locator("a").click()
                _curto()
                item_chosen.get_by_role("textbox").fill(_ITEM_LISTA_SERVICO)
                _curto()
                page.locator(f"[id*='ItemListaServico_chosen'] .chosen-results li:has-text('{_ITEM_LISTA_SERVICO}')").first.click()
            else:
                # Fallback: input direto
                campo_item = page.locator("[id*='ItemListaServico'], [id*='ListaServico']").first
                campo_item.click()
                _curto()
                campo_item.fill(_ITEM_LISTA_SERVICO)
            _curto()
            _screenshot(page, "servico_item_selecionado")

            # Descrição / discriminação
            campo_desc = page.locator("#Descricao, #Discriminacao, textarea[id*='descricao' i], textarea[id*='discrimina' i]").first
            campo_desc.click()
            _curto()
            campo_desc.fill(self._montar_descricao(request))
            _curto()

            # Município de incidência do serviço
            mun_chosen = page.locator(
                "[id*='CodigoMunicipio_chosen'], [id*='MunicipioIncidencia_chosen'], "
                "[id*='MunicipioPrestacao_chosen']"
            ).first
            mun_chosen.locator("a").filter(has_text="Selecione").click()
            _curto()
            mun_chosen.get_by_role("textbox").fill(_MUNICIPIO_PRESTACAO)
            _curto()
            page.locator(
                "[id*='CodigoMunicipio_chosen'] .chosen-results li:has-text('Campinas'), "
                "[id*='MunicipioIncidencia_chosen'] .chosen-results li:has-text('Campinas'), "
                "[id*='MunicipioPrestacao_chosen'] .chosen-results li:has-text('Campinas')"
            ).first.click()
            _medio()
            _screenshot(page, "servico_preenchido")
            logger.info("Dados do serviço preenchidos")
        except NfseEmissionError:
            raise
        except Exception as exc:
            _screenshot(page, "servico_erro")
            raise NfseEmissionError(f"Falha ao preencher serviço: {exc}", stage="servico") from exc

    # ── valores ───────────────────────────────────────────────────────────────

    def _preencher_valores(self, page: Page, request: EmissionRequest) -> None:
        logger.info("Preenchendo valor: R$ {:.2f}", request.value)
        try:
            valor_fmt = f"{request.value:.2f}".replace(".", ",")

            campo_valor = page.locator("#ValorServico, [id*='ValorServicos'], [id*='ValorServico']").first
            campo_valor.click()
            _curto()
            campo_valor.fill(valor_fmt)
            _curto()
            _screenshot(page, "valores_preenchidos")
            logger.info("Valor preenchido: R$ {}", valor_fmt)
        except NfseEmissionError:
            raise
        except Exception as exc:
            _screenshot(page, "valores_erro")
            raise NfseEmissionError(f"Falha ao preencher valores: {exc}", stage="valores") from exc

    # ── emissão ───────────────────────────────────────────────────────────────

    def _emitir(self, page: Page) -> None:
        logger.info("Emitindo NFS-e...")
        try:
            page.get_by_role("button", name="Emitir").click()
            page.wait_for_load_state("networkidle", timeout=30_000)
            _longo()
            _screenshot(page, "pos_emissao")
            logger.info("NFS-e emitida")
        except NfseEmissionError:
            raise
        except Exception as exc:
            _screenshot(page, "emissao_erro")
            raise NfseEmissionError(f"Falha ao emitir: {exc}", stage="submit") from exc

    # ── extração e download ───────────────────────────────────────────────────

    def _extrair_numero_nota(self, page: Page) -> str | None:
        for selector in [
            "td:has-text('Número')",
            "span[id*='numero']",
            "strong:has-text('Número')",
            "[id*='NumeroNfse']",
        ]:
            try:
                el = page.locator(selector).first
                if el.is_visible():
                    numbers = re.findall(r"\d+", el.text_content() or "")
                    if numbers:
                        logger.info("Número da nota: {}", numbers[0])
                        return numbers[0]
            except Exception:
                continue
        logger.warning("Número da nota não encontrado")
        return None

    def _baixar_pdf(self, page: Page, numero: str | None) -> Path | None:
        logger.info("Baixando PDF...")
        sufixo = numero or datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = settings.pdf_path / f"nfse_{sufixo}.pdf"
        try:
            with page.expect_download(timeout=30_000) as dl:
                page.locator("a[href*='DANFSe'], a[href*='danfse' i], a:has-text('PDF')").first.click()
            dl.value.save_as(str(dest))
            logger.info("PDF salvo: {}", dest.name)
            return dest
        except Exception as exc:
            _screenshot(page, "download_erro")
            logger.warning("Falha ao baixar PDF: {}", exc)
            return None

    @staticmethod
    def _montar_descricao(request: EmissionRequest) -> str:
        return (
            f"Nota referente aos serviços de entregas prestados "
            f"no período de {request.period}."
        )
