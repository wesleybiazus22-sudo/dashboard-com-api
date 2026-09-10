"""
Cliente para a Meta Conversions API (CAPI) -- ENVIA eventos de conversao pro Pixel,
ao contrario de client.py (que so LE dados de campanha da Marketing API). Usado pra
avisar o Meta quando uma negociacao do RD CRM alcanca uma etapa avancada (ver
`notify_stage_reached` e o gatilho em webhooks/processor.py), fechando o loop entre
o clique original no anuncio (`fbclid`) e o que aconteceu depois no funil de vendas
-- informacao que a Marketing API sozinha nunca teria, porque o "lead" que ela
enxerga e so o clique/formulario, nao o que aconteceu depois no CRM.

Autenticacao: token de ESCRITA gerado no Gerenciador de Eventos especificamente pra
API de Conversoes -- NAO reaproveita o `meta_access_token` da Marketing API (aquele
so tem `ads_read`, sem permissao de gravar evento nenhum).
"""

import hashlib
import time

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from config.settings import require_meta_capi_credentials, settings

_BASE_URL = "https://graph.facebook.com"


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


def _hash(value: str) -> str:
    """Meta exige PII (email/telefone) em SHA-256, minusculo e sem espaco nas
    pontas -- ver 'Advanced Matching' na documentacao da Conversions API."""
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def format_fbc(fbclid: str, click_time: "int | None" = None) -> str:
    """Monta o parametro `fbc` a partir do fbclid cru (formato exigido:
    fb.{subdomain_index}.{timestamp_ms}.{fbclid} -- subdomain_index e praticamente
    sempre 1 na pratica, documentado assim pelo Meta).

    `click_time` e o timestamp (epoch, segundos) de quando o clique aconteceu. Como
    hoje nao guardamos o instante exato do clique (so quando a negociacao foi criada
    no CRM, que e MAIS TARDE que o clique de verdade), usar `click_time=None` cai no
    instante atual -- funciona pra fins de correspondencia (o Meta casa pelo valor
    do fbclid em si, o timestamp e metadado), mas nao e o carimbo real do clique."""
    ts_ms = int((click_time or time.time()) * 1000)
    return f"fb.1.{ts_ms}.{fbclid}"


class MetaConversionsApiClient:
    def __init__(self):
        require_meta_capi_credentials()
        self.pixel_id = settings.meta_capi_pixel_id
        self.access_token = settings.meta_capi_access_token
        self.api_version = settings.meta_api_version

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    def _post(self, body: dict) -> dict:
        url = f"{_BASE_URL}/{self.api_version}/{self.pixel_id}/events"
        response = httpx.post(url, params={"access_token": self.access_token}, json=body, timeout=30)
        if response.status_code >= 400:
            try:
                detalhe = response.json().get("error", {})
                msg = detalhe.get("message") or response.text
            except ValueError:
                msg = response.text
            raise httpx.HTTPStatusError(
                f"Meta Conversions API [{response.status_code}]: {msg}",
                request=response.request, response=response,
            )
        return response.json()

    def send_event(
        self,
        *,
        event_name: str,
        event_time: int,
        event_id: str,
        fbclid: str | None = None,
        fbc: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        event_source_url: str | None = None,
        action_source: str = "website",
    ) -> dict:
        """Envia UM evento de conversao. `event_id` e a chave de deduplicacao do
        Meta -- reenviar o mesmo `event_id` (ex: mesmo negocio batendo o webhook
        2x por retry) nao conta como 2 eventos.

        `action_source="website"` (default): o lead ORIGINOU no site (clicou no
        anuncio -> caiu na landing -> virou negociacao). O evento aqui e um
        marco POSTERIOR do mesmo lead (chegou em "Reuniao Agendada" no CRM), mas
        e a mesma jornada que comecou no site -- e com `fbc` (do fbclid) +
        `event_source_url` o Meta consegue atribuir a conversao a campanha de
        origem e MOSTRAR no relatorio de anuncios.

        `action_source="system_generated"` (usado antes) e so pra evento SEM
        nenhuma interacao de usuario (renovacao automatica, notificacao de
        sistema) -- o Meta atribui esses de forma bem limitada e eles nao
        aparecem no relatorio de campanha, que era exatamente o sintoma."""
        user_data: dict = {}
        if fbc:
            user_data["fbc"] = fbc
        elif fbclid:
            user_data["fbc"] = format_fbc(fbclid)
        if email:
            user_data["em"] = [_hash(email)]
        if phone:
            # Meta espera telefone em E.164 sem "+" antes de hashear -- quem chama
            # e responsavel por normalizar (ver `_normaliza_telefone` no ponto de uso).
            user_data["ph"] = [_hash(phone)]

        evento = {
            "event_name": event_name,
            "event_time": event_time,
            "event_id": event_id,
            "action_source": action_source,
            "user_data": user_data,
        }
        if event_source_url:
            evento["event_source_url"] = event_source_url

        return self._post({"data": [evento]})
