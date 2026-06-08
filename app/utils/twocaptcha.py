"""
app/utils/twocaptcha.py
Resolve hCaptcha via 2captcha API (https://2captcha.com).
"""
import time

import requests
from loguru import logger

_BASE = "https://2captcha.com"
_POLL_INTERVAL = 5
_MAX_ATTEMPTS  = 96  # ~8 minutos


def solve_hcaptcha(api_key: str, site_key: str, page_url: str) -> str | None:
    if not api_key:
        logger.warning("TWOCAPTCHA_API_KEY não configurada")
        return None

    logger.info("2captcha: enviando tarefa hCaptcha para {}", page_url)
    try:
        resp = requests.post(
            f"{_BASE}/in.php",
            data={
                "key": api_key,
                "method": "hcaptcha",
                "sitekey": site_key,
                "pageurl": page_url,
                "json": 1,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("2captcha submit falhou: {}", exc)
        return None

    if data.get("status") != 1:
        logger.warning("2captcha erro no submit: {}", data.get("request"))
        return None

    task_id = data["request"]
    logger.info("2captcha: taskId={} — aguardando solução...", task_id)

    for attempt in range(_MAX_ATTEMPTS):
        time.sleep(_POLL_INTERVAL)
        try:
            poll = requests.get(
                f"{_BASE}/res.php",
                params={"key": api_key, "action": "get", "id": task_id, "json": 1},
                timeout=15,
            )
            poll.raise_for_status()
            result = poll.json()
        except Exception as exc:
            logger.warning("2captcha poll falhou (tentativa {}): {}", attempt + 1, exc)
            continue

        if result.get("status") == 1:
            token = result.get("request")
            logger.info("2captcha: resolvido em ~{}s", (attempt + 1) * _POLL_INTERVAL)
            return token

        if result.get("request") != "CAPCHA_NOT_READY":
            logger.warning("2captcha erro: {}", result.get("request"))
            return None

    logger.warning("2captcha: timeout após {} tentativas", _MAX_ATTEMPTS)
    return None
