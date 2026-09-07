"""
Sincroniza o custo REAL de mensageria do WhatsApp, direto do endpoint de
analytics de conversas do WABA -- e o numero oficial que o Meta fatura, entao
nao tentamos reconstruir a logica de janela de 24h/categoria de conversa por
conta propria (tem detalhes proprios -- ex: a primeira conversa em 24h dentro
de "atendimento" pode ser gratuita dependendo da categoria -- que mudam com
o tempo e seriam faceis de errar).

Validado contra a API real (200 OK, sintaxe correta) -- so ainda sem dado
porque o numero e novo e nao teve conversa cobravel ainda. O formato do
payload abaixo segue a documentacao oficial da Conversation Analytics API.
"""

import time
from datetime import date, datetime, timezone

import httpx
from sqlalchemy.orm import Session
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from config.settings import require_whatsapp_credentials, settings
from database.models import WhatsappConversationCost

_API_VERSION = "v21.0"


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    reraise=True,
)
def _buscar_conversation_analytics(start: int, end: int) -> dict:
    require_whatsapp_credentials()
    url = f"https://graph.facebook.com/{_API_VERSION}/{settings.whatsapp_business_account_id}"
    campo = (
        f'conversation_analytics.start({start}).end({end}).granularity(DAILY)'
        '.phone_numbers([]).dimensions(["conversation_category","conversation_type","phone"])'
    )
    response = httpx.get(url, params={"fields": campo, "access_token": settings.whatsapp_access_token}, timeout=30)
    if response.status_code == 429:
        time.sleep(10)
    if response.status_code >= 400:
        try:
            detalhe = response.json().get("error", {})
            msg = detalhe.get("error_user_msg") or detalhe.get("message") or response.text
        except ValueError:
            msg = response.text
        raise httpx.HTTPStatusError(
            f"WhatsApp Conversation Analytics [{response.status_code}]: {msg}",
            request=response.request, response=response,
        )
    return response.json()


def sync_conversation_cost(db: Session, *, dias: int = 30) -> int:
    """Busca e grava o custo de conversa dos ultimos `dias` dias. Reprocessa a
    janela inteira a cada rodada (upsert por dia+categoria+tipo+numero) em vez
    de so pegar "o que e novo" -- o Meta pode fechar/ajustar o numero de
    conversas de um dia alguns dias depois (parecido com o motivo de
    reprocessarmos insights do Meta Ads em janela rolante)."""
    agora = int(datetime.now(timezone.utc).timestamp())
    inicio = agora - dias * 24 * 3600

    payload = _buscar_conversation_analytics(inicio, agora)
    analytics = (payload.get("conversation_analytics") or {}).get("data") or []

    count = 0
    for bloco in analytics:
        for ponto in bloco.get("data_points", []):
            dia = date.fromtimestamp(ponto["start"], tz=timezone.utc)
            chave = {
                "date": dia,
                "conversation_category": ponto.get("conversation_category") or "(desconhecida)",
                "conversation_type": ponto.get("conversation_type") or "(desconhecido)",
                "phone_number": ponto.get("phone_number"),
            }
            existente = (
                db.query(WhatsappConversationCost)
                .filter_by(**chave)
                .one_or_none()
            )
            if existente is None:
                existente = WhatsappConversationCost(**chave)
                db.add(existente)
            existente.conversation_count = ponto.get("conversation", 0)
            existente.cost_usd = ponto.get("cost", 0.0)
            existente.synced_at = datetime.now(timezone.utc)
            db.flush()
            count += 1

    db.commit()
    return count
