"""
Cliente para a GA4 Data API (Google Analytics 4 -- relatorios agregados).

Diferente do RD CRM e do Meta Ads (que expoem ENTIDADES individuais com id proprio,
paginadas por cursor/pagina), a GA4 Data API so expoe RELATORIOS ja agregados: cada
chamada a `runReport` devolve linhas somarizadas por uma combinacao de dimensoes
(ex: data + pais), sem id proprio -- a chave natural de cada linha e a propria
combinacao de dimensoes (ver `upsert_by_composite` em entities.py).

Autenticacao via CONTA DE SERVICO (JWT assinado localmente e trocado por um access
token de curta duracao direto no Google) -- ao contrario do Meta, nao ha token fixo
pra guardar/renovar manualmente: a biblioteca `google-auth` cuida disso sozinha a
cada chamada (`Credentials.refresh`).
"""

import json
import time

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from config.settings import require_ga4_credentials, settings

SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]
_BASE_URL = "https://analyticsdata.googleapis.com/v1beta"

# Tamanho de pagina por chamada -- bem acima do volume de linhas que qualquer
# relatorio deste dashboard deve produzir por rodada, mas pagina via offset mesmo
# assim (ver `run_report`) pra nao truncar silenciosamente se um dia isso mudar.
_PAGE_SIZE = 100000


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


class Ga4Client:
    def __init__(self):
        require_ga4_credentials()
        info = json.loads(settings.ga4_service_account_json)
        self._credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        self._property = f"properties/{settings.ga4_property_id}"

    def _access_token(self) -> str:
        if not self._credentials.valid:
            self._credentials.refresh(GoogleAuthRequest())
        return self._credentials.token

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    def _post(self, path: str, body: dict) -> dict:
        response = httpx.post(
            f"{_BASE_URL}/{path}",
            headers={"Authorization": f"Bearer {self._access_token()}"},
            json=body,
            timeout=60,
        )
        if response.status_code == 429:
            time.sleep(10)
        if response.status_code >= 400:
            try:
                detalhe = response.json().get("error", {})
                msg = detalhe.get("message") or response.text
            except ValueError:
                msg = response.text
            raise httpx.HTTPStatusError(
                f"GA4 Data API [{response.status_code}]: {msg}",
                request=response.request,
                response=response,
            )
        return response.json()

    def run_report(self, *, dimensions: list[str], metrics: list[str], start_date: str, end_date: str) -> list[dict]:
        """Roda um relatorio e devolve uma lista de dicts {nome_da_dimensao_ou_metrica:
        valor}. A API devolve TODO valor como string (mesmo numeros) -- ver os parsers
        em entities.py. Pagina via offset se um relatorio tiver mais linhas que
        `_PAGE_SIZE`."""
        linhas: list[dict] = []
        offset = 0
        while True:
            body = {
                "dateRanges": [{"startDate": start_date, "endDate": end_date}],
                "dimensions": [{"name": d} for d in dimensions],
                "metrics": [{"name": m} for m in metrics],
                "limit": _PAGE_SIZE,
                "offset": offset,
            }
            payload = self._post(f"{self._property}:runReport", body)
            dim_names = [h["name"] for h in payload.get("dimensionHeaders", [])]
            met_names = [h["name"] for h in payload.get("metricHeaders", [])]
            for row in payload.get("rows", []):
                item = {}
                for name, dv in zip(dim_names, row.get("dimensionValues", [])):
                    item[name] = dv.get("value")
                for name, mv in zip(met_names, row.get("metricValues", [])):
                    item[name] = mv.get("value")
                linhas.append(item)

            total = int(payload.get("rowCount", 0))
            offset += _PAGE_SIZE
            if offset >= total or not payload.get("rows"):
                break
        return linhas
