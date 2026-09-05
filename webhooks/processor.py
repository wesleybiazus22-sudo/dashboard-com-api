"""
Traduz webhooks crm_deal_created / crm_deal_updated em historico analitico:
- fecha/abre linhas em crm_deal_stage_history quando a etapa muda (aging/velocity)
- fecha/abre linhas em crm_deal_owner_history quando o dono muda, e deduz
  SDR (primeiro dono) x Closer (proximo dono distinto) automaticamente
- grava um crm_deal_event por campo relevante que mudou (auditoria / timeline)

ATENCAO: o formato exato do payload do webhook do RD CRM (se o objeto da negociacao
vem em payload['data'], payload['deal'] ou no proprio payload) deve ser confirmado
contra um evento real assim que voce cadastrar o primeiro webhook. Ajuste
`_extract_deal_payload` se necessario -- e o unico lugar que precisa mudar.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from config.settings import settings
from database.models import CrmContact, CrmDeal
from ingestion.meta_ads.capi import MetaConversionsApiClient
from ingestion.rd_crm.deal_history import apply_sdr_closer_split, snapshot_deal, sync_deal_history
from ingestion.rd_crm.deals import extract_deal_fields
from ingestion.rd_crm.entities import upsert_by_rd_id

__all__ = ["apply_sdr_closer_split", "process_deal_webhook"]

logger = logging.getLogger(__name__)


def _notify_meta_capi(db: Session, deal: CrmDeal, at: datetime) -> None:
    """Envia o evento de conversao pro Meta quando a negociacao ACABOU DE ENTRAR na
    etapa-gatilho (ver META_CAPI_TRIGGER_STAGE_RD_ID). Nunca deixa uma falha aqui
    derrubar o processamento do webhook em si -- o dado do CRM (stage_history,
    eventos) e a responsabilidade PRIMARIA desta funcao no arquivo; notificar o
    Meta e um efeito colateral bem-vindo, mas secundario. Por isso qualquer
    problema (credencial ausente, fbclid ausente, erro de rede) so gera um log e
    segue em frente, nunca uma excecao."""
    if not settings.meta_capi_pixel_id or not settings.meta_capi_access_token:
        return  # CAPI nao configurada ainda -- silencioso de proposito (ver README secao 14)

    if not deal.fbclid:
        logger.info("CAPI: negociacao %s entrou na etapa-gatilho sem fbclid -- pulando.", deal.rd_id)
        return

    contact = None
    if deal.contact_rd_id:
        contact = db.query(CrmContact).filter(CrmContact.rd_id == deal.contact_rd_id).one_or_none()

    try:
        client = MetaConversionsApiClient()
        client.send_event(
            event_name=settings.meta_capi_event_name,
            event_time=int(at.timestamp()),
            event_id=f"{deal.rd_id}:{settings.meta_capi_trigger_stage_rd_id}",
            fbclid=deal.fbclid,
            email=contact.email if contact else None,
            phone=contact.phone if contact else None,
        )
        logger.info("CAPI: evento '%s' enviado pra negociacao %s.", settings.meta_capi_event_name, deal.rd_id)
    except Exception:  # noqa: BLE001 -- ver docstring: nunca deixa isso quebrar o webhook
        logger.exception("CAPI: falha ao enviar evento pra negociacao %s.", deal.rd_id)


def _extract_deal_payload(payload: dict) -> dict:
    return payload.get("data") or payload.get("deal") or payload


def _now() -> datetime:
    return datetime.now(timezone.utc)


def process_deal_webhook(db: Session, event_type: str, payload: dict) -> None:
    deal_payload = _extract_deal_payload(payload)
    rd_id = deal_payload.get("id")
    if not rd_id:
        raise ValueError("Payload do webhook sem 'id' de negociacao.")

    new_fields = extract_deal_fields(deal_payload)
    at = new_fields["deal_updated_at"] or _now()

    previous = snapshot_deal(db.query(CrmDeal).filter(CrmDeal.rd_id == rd_id).one_or_none())

    deal = upsert_by_rd_id(db, CrmDeal, rd_id, new_fields)
    db.flush()  # garante deal.id disponivel para as linhas de historico abaixo

    sync_deal_history(db, previous, deal, new_fields, at)
    db.commit()

    # So dispara pro Meta quando a negociacao ACABOU DE ENTRAR na etapa-gatilho
    # nesta mudanca (previous.stage != nova stage) -- sem o "previous is not None"
    # aqui, toda negociacao NOVA criada ja na etapa-gatilho tambem dispararia, o
    # que ainda faz sentido (ela "entrou" na etapa agora), mas repetir o mesmo
    # evento em UPDATEs subsequentes que nao mudam de etapa seria ruido -- por
    # isso o `stage_rd_id` precisa ter mudado (ou nao existir historico anterior).
    entrou_na_etapa_gatilho = (
        new_fields.get("stage_rd_id") == settings.meta_capi_trigger_stage_rd_id
        and (previous is None or previous.get("stage_rd_id") != new_fields.get("stage_rd_id"))
    )
    if entrou_na_etapa_gatilho:
        _notify_meta_capi(db, deal, at)
