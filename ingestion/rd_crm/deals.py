"""
Sincronização de negociações (deals) do RD CRM.

Formato confirmado via `python -m scripts.dump_sample deals` (payload real): os campos
sao diretos no objeto da negociacao -- pipeline_id, stage_id, owner_id, organization_id,
lost_reason_id, source_id, contact_ids (lista; usamos o primeiro como contato principal)
-- e nao objetos aninhados como "deal_stage"/"organization", que era a suposicao inicial
e causava 401/dados vazios. Valor monetario vem em `total_price` (nao ha campo "amount").
Campos ainda nao observados num payload real (campaign_id, expected_close_date) mantem
fallback defensivo -- reconfirme com `dump_sample` se algo mudar.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from database.models import CrmDeal
from ingestion.rd_crm.client import RDCrmClient
from ingestion.rd_crm.deal_history import snapshot_deal, sync_deal_history
from ingestion.rd_crm.entities import parse_dt, upsert_by_rd_id

ENDPOINT = "/deals"


def extract_deal_fields(item: dict) -> dict:
    return {
        "name": item.get("name"),
        "amount": item.get("amount") if item.get("amount") is not None else item.get("total_price"),
        "currency": item.get("currency") or "BRL",
        "pipeline_rd_id": item.get("pipeline_id"),
        "stage_rd_id": item.get("stage_id"),
        "status": item.get("status") or item.get("deal_status"),  # open / won / lost
        "organization_rd_id": item.get("organization_id"),
        "contact_rd_id": item.get("contact_id") or (item.get("contact_ids") or [None])[0],
        "current_owner_rd_id": item.get("owner_id"),
        "campaign": item.get("campaign_id"),
        "source": item.get("source_id"),
        "lost_reason_rd_id": item.get("lost_reason_id"),
        "deal_created_at": parse_dt(item.get("created_at")),
        "deal_updated_at": parse_dt(item.get("updated_at")),
        "closed_at": parse_dt(item.get("closed_at") or item.get("won_at") or item.get("lost_at")),
        "expected_close_date": parse_dt(item.get("expected_close_date")),
        "raw": item,
    }


def _sync(db: Session, params: dict | None) -> int:
    client = RDCrmClient(db)
    count = 0

    for item in client.paginate(ENDPOINT, params=params):
        fields = extract_deal_fields(item)
        previous = snapshot_deal(db.query(CrmDeal).filter(CrmDeal.rd_id == item["id"]).one_or_none())
        deal = upsert_by_rd_id(db, CrmDeal, item["id"], fields)
        db.flush()  # garante deal.id preenchido antes de referenciar em history
        at = fields["deal_updated_at"] or datetime.now(timezone.utc)
        sync_deal_history(db, previous, deal, fields, at)
        count += 1

    db.commit()
    return count


def sync_deals_full(db: Session) -> int:
    """Carga inicial: busca TODAS as negociações, sem filtro de data."""
    return _sync(db, params=None)


def sync_deals_incremental(db: Session, updated_since_iso: str) -> int:
    """Carga incremental: só negociações atualizadas desde a última sincronização.

    A API usa RDQL no parâmetro `filter` (não um `updated_at` solto): o operador
    "maior que" é `campo:>valor`.
    """
    return _sync(db, params={"filter": f"updated_at:>{updated_since_iso}"})
