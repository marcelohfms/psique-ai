"""Chamada do painel aos endpoints internos da Eva (/admin/*).

Env: EVA_BASE_URL (ex.: https://psiqueai.ayexa.com.br) e ADMIN_SECRET, o mesmo
valor configurado na Eva."""
import logging
import os

import httpx

logger = logging.getLogger(__name__)


class EvaUnavailable(Exception):
    """Eva fora do ar, sem configuração, ou resposta ilegível."""


async def post(path: str, body: dict, timeout: float = 30.0) -> tuple[int, dict]:
    base = os.getenv("EVA_BASE_URL", "").rstrip("/")
    secret = os.getenv("ADMIN_SECRET", "")
    if not base or not secret:
        raise EvaUnavailable("painel sem EVA_BASE_URL/ADMIN_SECRET")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{base}{path}", json=body, headers={"X-Admin-Secret": secret})
        return r.status_code, r.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception("eva_client: falha em %s", path)
        raise EvaUnavailable(str(exc)) from exc
