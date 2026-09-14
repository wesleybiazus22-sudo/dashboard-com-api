"""
Cliente pro Microsoft Graph API (calendario/Teams) -- usado pelo agente pra
verificar disponibilidade e criar a reuniao de verdade na agenda do dono da
negociacao quando o lead confirma um horario (ver
ingestion/llm/agent.py::confirmar_reuniao).

Autenticacao: client credentials flow (de APLICATIVO, nao de usuario) -- pede
um App Registration no Azure AD do tenant com a permissao "Calendars.ReadWrite"
do tipo APPLICATION (nao "Delegated") com consentimento de admin. Com isso o
app consegue agir na agenda de QUALQUER usuario do dominio via
`/users/{email}/...`, sem cada pessoa precisar logar/consentir individualmente
-- mesmo principio do Usuario do Sistema do Meta e do token do RD CRM (ver
docstring de config/settings.py).

VALIDADO contra o tenant real em 2026-09-12: token via client credentials,
`verificar_disponibilidade` nas agendas de miria.martins@ e wesley.hardt@, e
`criar_evento` (evento de teste na agenda da Miria, com link do Teams
confirmado em `onlineMeeting.joinUrl` -- exatamente o campo que este client
le). App Registration "Agente Máquina ISP - Agenda" no Entra ID, permissao
"Calendars.ReadWrite" tipo APPLICATION com consentimento de admin ja
concedido.
"""

from datetime import datetime

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from config.settings import require_microsoft_credentials, settings

_GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
_TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_FUSO = "America/Sao_Paulo"


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


class MicrosoftCalendarClient:
    def __init__(self):
        require_microsoft_credentials()
        self._access_token = self._obter_token()

    def _obter_token(self) -> str:
        """Client credentials flow -- token de APLICATIVO (sem tela de login,
        sem sessao de usuario pra expirar). Pede um token novo a cada
        instancia do client em vez de cachear entre chamadas -- simples, e o
        agente cria o client no maximo uma vez por turno de conversa."""
        resposta = httpx.post(
            _TOKEN_URL_TEMPLATE.format(tenant=settings.microsoft_tenant_id),
            data={
                "client_id": settings.microsoft_client_id,
                "client_secret": settings.microsoft_client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
            timeout=30,
        )
        resposta.raise_for_status()
        return resposta.json()["access_token"]

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    def _request(self, method: str, path: str, **kwargs) -> dict:
        resposta = httpx.request(
            method,
            f"{_GRAPH_BASE_URL}{path}",
            headers={"Authorization": f"Bearer {self._access_token}", "Content-Type": "application/json"},
            timeout=30,
            **kwargs,
        )
        resposta.raise_for_status()
        return resposta.json() if resposta.content else {}

    def verificar_disponibilidade(self, email_organizador: str, inicio: datetime, fim: datetime) -> bool:
        """True se o intervalo [inicio, fim) estiver livre na agenda de
        `email_organizador`. Usa `getSchedule` (funciona com permissao de
        APLICATIVO -- ao contrario de `findMeetingTimes`, que exige contexto
        de usuario/delegated)."""
        corpo = {
            "schedules": [email_organizador],
            "startTime": {"dateTime": inicio.isoformat(), "timeZone": _FUSO},
            "endTime": {"dateTime": fim.isoformat(), "timeZone": _FUSO},
            "availabilityViewInterval": 30,
        }
        resposta = self._request("POST", f"/users/{email_organizador}/calendar/getSchedule", json=corpo)
        itens = (resposta.get("value") or [{}])[0].get("scheduleItems") or []
        return len(itens) == 0

    def criar_evento(
        self,
        *,
        email_organizador: str,
        assunto: str,
        inicio: datetime,
        fim: datetime,
        participantes: list[str] | None = None,
        corpo: str = "",
    ) -> dict:
        """Cria o evento na agenda de `email_organizador`, com link de
        reuniao do Teams gerado automaticamente (`isOnlineMeeting`). Cada
        e-mail em `participantes` (ex: o lead, e/ou uma segunda pessoa da
        Maquina.ISP cruzada via MICROSOFT_CALENDAR_CROSS_MAP) entra como
        convidado obrigatorio -- o organizador ja fica incluso automaticamente
        pelo proprio Graph, nao precisa (nem deve) estar nessa lista."""
        payload: dict = {
            "subject": assunto,
            "body": {"contentType": "HTML", "content": corpo},
            "start": {"dateTime": inicio.isoformat(), "timeZone": _FUSO},
            "end": {"dateTime": fim.isoformat(), "timeZone": _FUSO},
            "isOnlineMeeting": True,
            "onlineMeetingProvider": "teamsForBusiness",
        }
        if participantes:
            payload["attendees"] = [
                {"emailAddress": {"address": email}, "type": "required"} for email in participantes
            ]
        return self._request("POST", f"/users/{email_organizador}/events", json=payload)
