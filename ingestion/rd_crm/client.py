"""
Cliente HTTP para a API v2 do RD Station CRM.

- Injeta o access_token válido em cada chamada (renovando sozinho via ingestion.rd_crm.auth)
- Trata paginação automaticamente (paginate())
- Faz backoff simples em 429 (rate limit) e retry em erros 5xx transitórios
"""

import time
from typing import Iterator

import httpx
from sqlalchemy.orm import Session
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from config.settings import settings
from ingestion.rd_crm.auth import get_valid_access_token


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


class RDCrmClient:
    def __init__(self, db: Session):
        self.db = db
        self.base_url = settings.rd_crm_api_base_url.rstrip("/")

    def _headers(self) -> dict:
        token = get_valid_access_token(self.db)
        return {"Authorization": f"Bearer {token}"}

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    def get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        response = httpx.get(url, headers=self._headers(), params=params or {}, timeout=30)

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 5))
            time.sleep(retry_after)

        response.raise_for_status()
        return response.json()

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    def post(self, path: str, json: dict) -> dict:
        url = f"{self.base_url}{path}"
        response = httpx.post(url, headers=self._headers(), json=json, timeout=30)

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 5))
            time.sleep(retry_after)

        response.raise_for_status()
        return response.json()

    def paginate(self, path: str, params: dict | None = None, page_size: int = 200) -> Iterator[dict]:
        """
        Percorre todas as páginas de um endpoint de listagem do RD CRM v2 e produz
        (yield) um registro por vez. A API v2 pagina com `page[number]`/`page[size]`
        e devolve os itens dentro de `data` (estilo JSON:API). Mantemos um fallback
        para o formato antigo (`items`) e para respostas que já sejam uma lista pura.
        """
        params = dict(params or {})
        params["page[size]"] = page_size
        page_number = 1

        while True:
            params["page[number]"] = page_number
            data = self.get(path, params=params)

            items = data.get("data") if isinstance(data, dict) else None
            if items is None and isinstance(data, dict):
                items = data.get("items")
            if items is None and isinstance(data, dict):
                # fallback: primeira lista encontrada no payload
                items = next((v for v in data.values() if isinstance(v, list)), [])
            if items is None and isinstance(data, list):
                items = data

            if not items:
                break

            for item in items:
                yield item

            if len(items) < page_size:
                break

            page_number += 1
