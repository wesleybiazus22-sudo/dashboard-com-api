"""
Recebe os webhooks do WhatsApp Cloud API: o handshake de verificacao (GET, feito
uma vez quando voce cadastra a URL no app do Meta) e os eventos de verdade
(POST, toda mensagem/status que chega).

Mesma logica de "responde rapido, processa depois" do webhook do RD CRM (ver
api/routes/webhooks.py) -- o Meta espera 2XX em poucos segundos, senao considera
falha e reenvia.
"""

import hashlib
import hmac
import logging
from datetime import datetime
from secrets import compare_digest

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config.settings import settings
from database.connection import SessionLocal, get_db
from database.models import WhatsappWebhookEvent
from whatsapp.processor import processar_evento

router = APIRouter(prefix="/webhooks", tags=["whatsapp"])
logger = logging.getLogger(__name__)


@router.get("/whatsapp")
def verificar_webhook(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
    hub_challenge: str = Query(..., alias="hub.challenge"),
):
    """Handshake de verificacao: o Meta chama isso UMA VEZ quando voce salva a
    URL do webhook na tela de configuracao do app. Precisa devolver exatamente
    o `hub.challenge` recebido, como texto puro (nao JSON), pra confirmar que
    quem esta configurando controla mesmo este servidor."""
    esperado = settings.whatsapp_verify_token
    if not esperado or hub_mode != "subscribe" or not compare_digest(hub_verify_token, esperado):
        raise HTTPException(status_code=403, detail="Verify token invalido.")
    return Response(content=hub_challenge, media_type="text/plain")


def _assinatura_valida(corpo: bytes, assinatura_header: str | None) -> bool:
    """Confere a assinatura HMAC-SHA256 que o Meta manda em todo POST (header
    X-Hub-Signature-256), calculada com o APP SECRET. Sem isso, qualquer um que
    descobrisse a URL do webhook poderia mandar "mensagem" falsa que dispararia
    o agente e acoes no CRM em nome de um lead que nunca escreveu nada.

    Segredo vazio = validacao DESLIGADA (mesmo criterio de sempre neste projeto)
    -- so aceitavel durante desenvolvimento local, nunca em producao."""
    if not settings.whatsapp_app_secret:
        return True
    if not assinatura_header or not assinatura_header.startswith("sha256="):
        return False
    esperado = hmac.new(settings.whatsapp_app_secret.encode(), corpo, hashlib.sha256).hexdigest()
    recebido = assinatura_header.removeprefix("sha256=")
    return compare_digest(esperado, recebido)


def _processar_em_background(evento_id: str) -> None:
    db = SessionLocal()
    try:
        evento = db.get(WhatsappWebhookEvent, evento_id)
        if evento is None or evento.processed:
            return
        try:
            processar_evento(db, evento.payload)
            evento.processed = True
            evento.processed_at = datetime.utcnow()
            db.add(evento)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - registra o erro em vez de derrubar o worker
            db.rollback()
            evento = db.get(WhatsappWebhookEvent, evento_id)
            if evento is not None:
                evento.processing_error = str(exc)
                db.commit()
            logger.exception("Falha processando webhook do WhatsApp (evento_id=%s)", evento_id)
    finally:
        db.close()


@router.post("/whatsapp")
async def receber_webhook(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    corpo = await request.body()

    if not _assinatura_valida(corpo, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=401, detail="Assinatura invalida.")

    payload = await request.json()

    # Nao ha um id natural por REQUEST (so por mensagem, dentro do payload) --
    # usa hash do corpo cru como chave de deduplicacao, pra cobrir o caso do
    # Meta reenviar o mesmo POST inteiro por timeout/retry.
    dedupe_key = hashlib.sha256(corpo).hexdigest()

    evento = WhatsappWebhookEvent(dedupe_key=dedupe_key, payload=payload)
    db.add(evento)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"status": "duplicate_ignored"}

    db.refresh(evento)
    background_tasks.add_task(_processar_em_background, evento.id)
    return {"status": "received"}
