"""
Recebe os webhooks do RD Station CRM (crm_deal_created, crm_deal_updated, ...).

Responde 2XX imediatamente (o RD faz retry se nao receber isso rapido) e processa
o evento em background. `transaction_uuid` garante idempotencia: se o RD reenviar
o mesmo evento, ele e apenas confirmado e ignorado na segunda vez.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config.settings import settings
from database.connection import SessionLocal, get_db
from database.models import WebhookEvent
from webhooks.processor import process_deal_webhook

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _process_in_background(webhook_event_id: str) -> None:
    db = SessionLocal()
    try:
        event = db.get(WebhookEvent, webhook_event_id)
        if event is None or event.processed:
            return
        try:
            if event.event_type.startswith("crm_deal"):
                process_deal_webhook(db, event.event_type, event.payload)
            event.processed = True
            event.processed_at = datetime.utcnow()
            db.add(event)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - registramos o erro em vez de derrubar o worker
            db.rollback()
            event = db.get(WebhookEvent, webhook_event_id)
            if event is not None:
                event.processing_error = str(exc)
                db.commit()
    finally:
        db.close()


@router.post("/rd")
async def receive_rd_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    token: str = Query(..., description="Token combinado na URL cadastrada no RD Station."),
    db: Session = Depends(get_db),
):
    if token != settings.rd_webhook_token:
        raise HTTPException(status_code=401, detail="Token invalido.")

    payload = await request.json()
    event_type = payload.get("type") or payload.get("event") or "unknown"
    transaction_uuid = (
        payload.get("transaction_uuid")
        or payload.get("uuid")
        or payload.get("event_uuid")
        or str(uuid.uuid4())
    )

    event = WebhookEvent(transaction_uuid=transaction_uuid, event_type=event_type, payload=payload)
    db.add(event)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"status": "duplicate_ignored"}

    db.refresh(event)
    background_tasks.add_task(_process_in_background, event.id)
    return {"status": "received"}
