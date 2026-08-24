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

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from database.models import CrmDeal
from ingestion.rd_crm.deal_history import apply_sdr_closer_split, snapshot_deal, sync_deal_history
from ingestion.rd_crm.deals import extract_deal_fields
from ingestion.rd_crm.entities import upsert_by_rd_id

__all__ = ["apply_sdr_closer_split", "process_deal_webhook"]


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
