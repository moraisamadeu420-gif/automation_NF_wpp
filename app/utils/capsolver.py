"""
app/utils/capsolver.py
Resolve hCaptcha via CapSolver API (https://capsolver.com).
Usado no download do DANFSe do portal nfse.gov.br.
"""
import time

import requests
from loguru import logger

_BASE_URL = "https://api.capsolver.com"
_POLL_INTERVAL = 3   # segundos entre polling
_MAX_ATTEMPTS  = 20  # máximo ~60 segundos de espera


def solve_hcaptcha(api_key: str, site_key: str, page_url: str) -> str | None:
    """
    Resolve hCaptcha e retorna o token, ou None se falhar.

    Args:
        api_key:  Chave do CapSolver
        site_key: data-sitekey do widget hCaptcha na página
        page_url: URL da página onde o captcha está
    """
    if not api_key:
        logger.warning("CAPSOLVER_API_KEY não configurada — captcha não pode ser resolvido")
        return None

    # Tenta enterprise primeiro (portal usa hCaptcha com desafio de imagens)
    # e cai para o tipo padrão se não suportado
    for task_type in ("HCaptchaEnterpriseTaskProxyless", "HCaptchaTaskProxyless"):
        logger.info("CapSolver: tentando tipo '{}' para {}", task_type, page_url)
        try:
            resp = requests.post(
                f"{_BASE_URL}/createTask",
                json={
                    "clientKey": api_key,
                    "task": {
                        "type": task_type,
                        "websiteURL": page_url,
                        "websiteKey": site_key,
                    },
                },
                timeout=15,
            )
            if not resp.ok:
                logger.warning("CapSolver createTask HTTP {}: {}", resp.status_code, resp.text[:300])
                continue
            data = resp.json()
            if data.get("errorId", 0) != 0:
                logger.warning("CapSolver erro ({}): {} — {}", task_type, data.get("errorCode"), data.get("errorDescription"))
                continue
            # Saiu sem erro — usa esse tipo
            break
        except Exception as exc:
            logger.warning("CapSolver createTask falhou ({}): {}", task_type, exc)
            continue
    else:
        return None

    task_id = data.get("taskId")
    if not task_id:
        logger.warning("CapSolver não retornou taskId")
        return None

    logger.info("CapSolver: taskId={} — aguardando solução...", task_id)

    for attempt in range(_MAX_ATTEMPTS):
        time.sleep(_POLL_INTERVAL)
        try:
            poll = requests.post(
                f"{_BASE_URL}/getTaskResult",
                json={"clientKey": api_key, "taskId": task_id},
                timeout=15,
            )
            poll.raise_for_status()
            result = poll.json()
        except Exception as exc:
            logger.warning("CapSolver getTaskResult falhou (tentativa {}): {}", attempt + 1, exc)
            continue

        status = result.get("status", "")
        if status == "ready":
            token = result.get("solution", {}).get("gRecaptchaResponse") or \
                    result.get("solution", {}).get("token")
            if token:
                logger.info("CapSolver: captcha resolvido em ~{}s", (attempt + 1) * _POLL_INTERVAL)
                return token
            logger.warning("CapSolver: status ready mas sem token na resposta")
            return None
        elif status == "failed":
            logger.warning("CapSolver: tarefa falhou — {}", result.get("errorDescription"))
            return None
        # status == "processing" — continua aguardando

    logger.warning("CapSolver: timeout após {} tentativas", _MAX_ATTEMPTS)
    return None
