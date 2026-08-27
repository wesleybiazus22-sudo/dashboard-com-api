"""
Cliente HTTP para a Graph API do Meta (Marketing API).

Diferencas relevantes em relacao ao RDCrmClient (ingestion/rd_crm/client.py):
- Autenticacao via `access_token` na query string (nao Authorization: Bearer) --
  e a forma documentada e universalmente compativel da Graph API.
- Paginacao por CURSOR: a resposta traz `paging.next`, uma URL COMPLETA e pronta
  pra proxima pagina (em vez de page[number] como no RD) -- so seguir esse link.
- Erros da Graph API costumam vir com HTTP 400 mesmo para problemas de permissao/
  token, dentro de um corpo `{"error": {...}}` -- raise_for_status() sozinho nao
  seria claro o suficiente, entao a mensagem de erro do Meta e propagada.

NUNCA gera/renova token aqui: o token e de um Usuario do Sistema configurado como
"Nunca expira" direto no Business Manager do Meta (ver README) -- nao ha fluxo de
refresh como o do RD CRM.
"""

import time
from typing import Iterator

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from config.settings import require_meta_credentials, settings


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


class MetaAdsClient:
    def __init__(self):
        require_meta_credentials()
        self.base_url = f"https://graph.facebook.com/{settings.meta_api_version}"
        self.ad_account_id = settings.meta_ad_account_id

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    def _get(self, url: str, params: dict | None = None) -> dict:
        response = httpx.get(url, params=params or {}, timeout=60)

        if response.status_code == 429:
            time.sleep(int(response.headers.get("Retry-After", 10)))

        if response.status_code >= 400:
            try:
                detalhe = response.json().get("error", {})
                msg = detalhe.get("message") or response.text
            except ValueError:
                msg = response.text
            raise httpx.HTTPStatusError(
                f"Meta Graph API [{response.status_code}]: {msg}",
                request=response.request,
                response=response,
            )
        return response.json()

    def get(self, path: str, params: dict | None = None) -> dict:
        """GET simples (uma pagina), com o access_token ja injetado."""
        params = dict(params or {})
        params["access_token"] = settings.meta_access_token
        return self._get(f"{self.base_url}{path}", params=params)

    def paginate(self, path: str, params: dict | None = None) -> Iterator[dict]:
        """Percorre todas as paginas seguindo `paging.next`, e produz (yield) um
        registro por vez a partir de `data`."""
        params = dict(params or {})
        params["access_token"] = settings.meta_access_token
        url = f"{self.base_url}{path}"

        while url:
            payload = self._get(url, params=params)
            for item in payload.get("data", []):
                yield item

            next_url = (payload.get("paging") or {}).get("next")
            # a partir da 2a pagina, os parametros ja estao embutidos na propria
            # `next_url` -- reenviar `params` de novo duplicaria access_token na
            # query string (a Graph API aceita, mas e desnecessario e fragil)
            url, params = next_url, None
