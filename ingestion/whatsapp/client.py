"""
Cliente pra ENVIAR mensagem via WhatsApp Cloud API. O recebimento fica em
`api/routes/whatsapp.py` (webhook) + `whatsapp/processor.py` -- este modulo so
cuida do lado de saida (respostas do agente, confirmacoes, etc.).

Autenticacao: token do Graph API (mesma familia dos outros produtos do Meta ja
usados neste projeto), passado como Bearer. Hoje `settings.whatsapp_access_token`
e um token TEMPORARIO do Graph API Explorer (poucas horas de vida) -- ver
`config/settings.py`. Trocar pelo token permanente do Usuario do Sistema assim
que a revisao do app aprovar `whatsapp_business_messaging`, sem precisar mudar
nada aqui.
"""

import re
import time

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from config.settings import require_whatsapp_credentials, settings

_BASE_URL = "https://graph.facebook.com"
_API_VERSION = "v21.0"


def normalizar_telefone_br(numero: str | None) -> str | None:
    """Converte um telefone como guardado no RD CRM (formato inconsistente --
    `+5511999998888`, `5511999998888` ou so `11999998888`, ja vistos na base
    real) pro formato que a WhatsApp Cloud API exige pra `to`: so digitos, com
    codigo do pais, sem "+". Devolve None se nao der pra confiar no numero
    (vazio, ou tamanho fora do esperado pra Brasil)."""
    if not numero:
        return None
    digitos = re.sub(r"\D", "", numero)
    if not digitos:
        return None
    if digitos.startswith("55") and len(digitos) in (12, 13):
        return digitos
    if len(digitos) in (10, 11):  # DDD + numero, sem o codigo do pais
        return "55" + digitos
    return None


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


class WhatsappClient:
    def __init__(self):
        require_whatsapp_credentials()
        self.phone_number_id = settings.whatsapp_phone_number_id
        self.access_token = settings.whatsapp_access_token

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        reraise=True,
    )
    def _post(self, body: dict) -> dict:
        url = f"{_BASE_URL}/{_API_VERSION}/{self.phone_number_id}/messages"
        response = httpx.post(
            url, headers={"Authorization": f"Bearer {self.access_token}"}, json=body, timeout=30,
        )
        if response.status_code == 429:
            time.sleep(int(response.headers.get("Retry-After", 10)))
        if response.status_code >= 400:
            try:
                detalhe = response.json().get("error", {})
                msg = detalhe.get("message") or response.text
            except ValueError:
                msg = response.text
            raise httpx.HTTPStatusError(
                f"WhatsApp Cloud API [{response.status_code}]: {msg}",
                request=response.request, response=response,
            )
        return response.json()

    def send_text(self, to: str, body: str, *, preview_url: bool = False) -> dict:
        """Manda texto livre. So funciona dentro da janela de 24h de atendimento
        (o contato precisa ter mandado mensagem nas ultimas 24h) -- fora dessa
        janela, o Meta rejeita e exige `send_template` em vez disso."""
        return self._post({
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body, "preview_url": preview_url},
        })

    def send_template(self, to: str, template_name: str, language_code: str = "pt_BR", components: list | None = None) -> dict:
        """Manda uma mensagem de modelo (template) pre-aprovada pelo Meta -- e o
        UNICO tipo de mensagem que pode iniciar conversa fora da janela de 24h
        (ex: primeiro contato depois de muito tempo, ou reabrir conversa)."""
        template: dict = {"name": template_name, "language": {"code": language_code}}
        if components:
            template["components"] = components
        return self._post({
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": template,
        })

    def mark_as_read(self, wamid: str) -> dict:
        """Marca uma mensagem recebida como lida (2 tiques azuis) -- puramente
        cosmetico pro lead, mas passa a impressao de atendimento ativo."""
        return self._post({
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": wamid,
        })
