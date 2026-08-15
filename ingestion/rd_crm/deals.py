"""
Sincronização de negociações (deals) do RD CRM.

IMPORTANTE sobre os nomes de campo abaixo (deal_stage, deal_pipeline, organization,
contacts, user, deal_source, deal_lost_reason...): eles seguem a nomenclatura mais comum
da API v2 do RD CRM, mas você DEVE validar contra um payload real antes de rodar em
produção. Rode `python -m scripts.dump_sample_deal` (ou inspecione a resposta de
GET /deals?limit=1 diretamente) e ajuste `extract_deal_fields` conforme necessário --
é o único lugar que precisa mudar.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from database.models import CrmDeal, CrmDealOwnerHistory, CrmDealStageHistory
from ingestion.rd_crm.client import RDCrmClient
from ingestion.rd_crm.entities import parse_dt, upsert_by_rd_id

ENDPOINT = "/deals"


def extract_deal_fields(item: dict) -> dict:
    stage = item.get("deal_stage") or {}
    pipeline = item.get("deal_pipeline") or {}
    organization = item.get("organization") or {}
    contacts = item.get("contacts") or []
    contact = contacts[0] if contacts else {}
    owner = item.get("user") or item.get("owner") or {}
    lost_reason = item.get("deal_lost_reason") or {}
    source = item.get("deal_source") or {}

    return {
        "name": item.get("name"),
        "amount": item.get("amount"),
        "currency": item.get("currency") or "BRL",
        "pipeline_rd_id": pipeline.get("id"),
        "stage_rd_id": stage.get("id"),
        "status": item.get("status") or item.get("deal_stage_state"),  # open / won / lost
        "organization_rd_id": organization.get("id"),
        "contact_rd_id": contact.get("id"),
        "current_owner_rd_id": owner.get("id"),
        "campaign": item.get("campaign"),
        "source": source.get("name"),
        "lost_reason_rd_id": lost_reason.get("id"),
        "deal_created_at": parse_dt(item.get("created_at")),
        "deal_updated_at": parse_dt(item.get("updated_at")),
        "closed_at": parse_dt(item.get("closed_at") or item.get("won_at") or item.get("lost_at")),
        "expected_close_date": parse_dt(item.get("prediction_date") or item.get("expected_close_at")),
        "raw": item,
    }


def _seed_history_if_missing(db: Session, deal: CrmDeal, fields: dict) -> None:
    """Na primeira carga não temos o histórico real de mudanças -- criamos uma linha
    'aberta' representando o estado atual, que será fechada e sucedida por novas linhas
    assim que os webhooks de crm_deal_updated começarem a chegar."""
    baseline_at = fields["deal_created_at"] or datetime.now(timezone.utc)

    has_stage_history = (
        db.query(CrmDealStageHistory).filter(CrmDealStageHistory.deal_rd_id == deal.rd_id).first()
    )
    if not has_stage_history and fields["stage_rd_id"]:
        db.add(
            CrmDealStageHistory(
                deal_id=deal.id,
                deal_rd_id=deal.rd_id,
                stage_rd_id=fields["stage_rd_id"],
                owner_rd_id=fields["current_owner_rd_id"],
                entered_at=baseline_at,
            )
        )

    has_owner_history = (
        db.query(CrmDealOwnerHistory).filter(CrmDealOwnerHistory.deal_rd_id == deal.rd_id).first()
    )
    if not has_owner_history and fields["current_owner_rd_id"]:
        db.add(
            CrmDealOwnerHistory(
                deal_id=deal.id,
                deal_rd_id=deal.rd_id,
                owner_rd_id=fields["current_owner_rd_id"],
                assigned_at=baseline_at,
            )
        )


def _sync(db: Session, params: dict | None) -> int:
    client = RDCrmClient(db)
    count = 0

    for item in client.paginate(ENDPOINT, params=params):
        fields = extract_deal_fields(item)
        deal = upsert_by_rd_id(db, CrmDeal, item["id"], fields)
        db.flush()  # garante deal.id preenchido antes de referenciar em history
        _seed_history_if_missing(db, deal, fields)
        count += 1

    db.commit()
    return count


def sync_deals_full(db: Session) -> int:
    """Carga inicial: busca TODAS as negociações, sem filtro de data."""
    return _sync(db, params=None)


def sync_deals_incremental(db: Session, updated_since_iso: str) -> int:
    """Carga incremental: só negociações atualizadas desde a última sincronização."""
    return _sync(db, params={"updated_at": updated_since_iso})
