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

from database.models import CrmDeal, CrmDealEvent, CrmDealOwnerHistory, CrmDealStageHistory
from ingestion.rd_crm.deals import extract_deal_fields
from ingestion.rd_crm.entities import upsert_by_rd_id

TRACKED_FIELDS = [
    "stage_rd_id",
    "pipeline_rd_id",
    "status",
    "amount",
    "lost_reason_rd_id",
    "current_owner_rd_id",
]


def _extract_deal_payload(payload: dict) -> dict:
    return payload.get("data") or payload.get("deal") or payload


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _close_open_stage_history(db: Session, deal_rd_id: str, at: datetime) -> None:
    open_row = (
        db.query(CrmDealStageHistory)
        .filter(CrmDealStageHistory.deal_rd_id == deal_rd_id, CrmDealStageHistory.exited_at.is_(None))
        .order_by(CrmDealStageHistory.entered_at.desc())
        .first()
    )
    if open_row:
        open_row.exited_at = at
        open_row.duration_seconds = int((at - open_row.entered_at).total_seconds())


def _close_open_owner_history(db: Session, deal_rd_id: str, at: datetime) -> None:
    open_row = (
        db.query(CrmDealOwnerHistory)
        .filter(CrmDealOwnerHistory.deal_rd_id == deal_rd_id, CrmDealOwnerHistory.unassigned_at.is_(None))
        .order_by(CrmDealOwnerHistory.assigned_at.desc())
        .first()
    )
    if open_row:
        open_row.unassigned_at = at


def _record_event(db: Session, deal: CrmDeal, field: str, old_value, new_value, at: datetime) -> None:
    db.add(
        CrmDealEvent(
            deal_id=deal.id,
            deal_rd_id=deal.rd_id,
            event_type="field_changed",
            field_changed=field,
            old_value=str(old_value) if old_value is not None else None,
            new_value=str(new_value) if new_value is not None else None,
            occurred_at=at,
        )
    )


def _apply_sdr_closer_split(db: Session, deal: CrmDeal, deal_rd_id: str) -> None:
    """O primeiro dono do historico = SDR que originou. O primeiro dono DIFERENTE
    que aparece depois = closer que recebeu o handoff."""
    owner_rows = (
        db.query(CrmDealOwnerHistory)
        .filter(CrmDealOwnerHistory.deal_rd_id == deal_rd_id)
        .order_by(CrmDealOwnerHistory.assigned_at.asc())
        .all()
    )
    if not owner_rows:
        return

    first_owner = owner_rows[0]
    deal.sdr_owner_rd_id = first_owner.owner_rd_id
    deal.sdr_assigned_at = first_owner.assigned_at

    handoff = next((row for row in owner_rows[1:] if row.owner_rd_id != first_owner.owner_rd_id), None)
    if handoff:
        deal.closer_owner_rd_id = handoff.owner_rd_id
        deal.handoff_at = handoff.assigned_at


def process_deal_webhook(db: Session, event_type: str, payload: dict) -> None:
    deal_payload = _extract_deal_payload(payload)
    rd_id = deal_payload.get("id")
    if not rd_id:
        raise ValueError("Payload do webhook sem 'id' de negociacao.")

    new_fields = extract_deal_fields(deal_payload)
    at = new_fields["deal_updated_at"] or _now()

    previous = db.query(CrmDeal).filter(CrmDeal.rd_id == rd_id).one_or_none()
    is_new = previous is None

    if not is_new:
        for field in TRACKED_FIELDS:
            old_value = getattr(previous, field)
            new_value = new_fields.get(field)
            if old_value != new_value:
                _record_event(db, previous, field, old_value, new_value, at)

        if previous.stage_rd_id != new_fields.get("stage_rd_id"):
            _close_open_stage_history(db, rd_id, at)
            db.add(
                CrmDealStageHistory(
                    deal_id=previous.id,
                    deal_rd_id=rd_id,
                    stage_rd_id=new_fields.get("stage_rd_id"),
                    pipeline_rd_id=new_fields.get("pipeline_rd_id"),
                    owner_rd_id=new_fields.get("current_owner_rd_id"),
                    entered_at=at,
                )
            )

        if previous.current_owner_rd_id != new_fields.get("current_owner_rd_id"):
            _close_open_owner_history(db, rd_id, at)
            db.add(
                CrmDealOwnerHistory(
                    deal_id=previous.id,
                    deal_rd_id=rd_id,
                    owner_rd_id=new_fields.get("current_owner_rd_id"),
                    assigned_at=at,
                )
            )

    deal = upsert_by_rd_id(db, CrmDeal, rd_id, new_fields)
    db.flush()  # garante deal.id disponivel para as linhas de historico abaixo

    if is_new and new_fields.get("stage_rd_id"):
        db.add(
            CrmDealStageHistory(
                deal_id=deal.id,
                deal_rd_id=rd_id,
                stage_rd_id=new_fields.get("stage_rd_id"),
                pipeline_rd_id=new_fields.get("pipeline_rd_id"),
                owner_rd_id=new_fields.get("current_owner_rd_id"),
                entered_at=new_fields.get("deal_created_at") or at,
            )
        )
    if is_new and new_fields.get("current_owner_rd_id"):
        db.add(
            CrmDealOwnerHistory(
                deal_id=deal.id,
                deal_rd_id=rd_id,
                owner_rd_id=new_fields.get("current_owner_rd_id"),
                assigned_at=new_fields.get("deal_created_at") or at,
            )
        )

    db.flush()
    _apply_sdr_closer_split(db, deal, rd_id)
    db.commit()
