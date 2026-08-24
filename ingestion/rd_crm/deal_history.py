"""
Deteccao e registro de transicoes de etapa/dono de negociacao.

Usado tanto pelo processamento de webhooks (crm_deal_updated) quanto pela
sincronizacao via polling (sync_all incremental/full) -- o webhook e o sinal
"em tempo real", mas nem sempre chega (Render free tier hiberna, webhook pode
ter sido cadastrado depois que a mudanca aconteceu, entrega pode falhar). Antes,
soh o webhook atualizava crm_deal_stage_history/crm_deal_owner_history; a
sincronizacao via polling so criava a linha "seed" na primeira vez que via a
negociacao e nunca mais tocava o historico, mesmo com a negociacao avancando de
etapa/dono nas sincronizacoes seguintes. Isso fazia o historico ficar
"congelado" no estado inicial, subestimando quantas negociacoes realmente
chegaram em "Reuniao Realizada"/"Freemium" (ver v_maquina_isp_deal_milestones).
"""

from datetime import datetime

from sqlalchemy.orm import Session

from database.models import CrmDeal, CrmDealEvent, CrmDealOwnerHistory, CrmDealStageHistory

TRACKED_FIELDS = [
    "stage_rd_id",
    "pipeline_rd_id",
    "status",
    "amount",
    "lost_reason_rd_id",
    "current_owner_rd_id",
]


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


def snapshot_deal(deal: CrmDeal | None) -> dict | None:
    """Tira uma foto dos campos rastreados ANTES do upsert. `upsert_by_rd_id`
    reaproveita a MESMA instancia ja carregada na sessao (identity map do
    SQLAlchemy) e muta seus atributos in-place -- sem essa foto em dict puro,
    'antes' e 'depois' seriam literalmente o mesmo objeto ja com os valores
    novos, e a comparacao de diff nunca detectaria mudanca nenhuma. Foi
    exatamente esse bug que deixava o historico de etapa/dono congelado."""
    if deal is None:
        return None
    return {field: getattr(deal, field) for field in TRACKED_FIELDS}


def apply_sdr_closer_split(db: Session, deal: CrmDeal, deal_rd_id: str) -> None:
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


def sync_deal_history(
    db: Session, previous: dict | None, deal: CrmDeal, new_fields: dict, at: datetime
) -> None:
    """Compara o estado anterior (snapshot tirado ANTES do upsert, via
    `snapshot_deal`) com os campos novos e:
    - grava um crm_deal_event por campo relevante que mudou
    - fecha a linha de stage/owner history aberta e abre uma nova quando a etapa
      ou o dono realmente mudou
    - se a negociacao e nova OU nao tem nenhuma linha de historico ainda (bug
      antigo / primeira vez vista), semeia uma linha "aberta" representando o
      estado atual
    - reaplica o split SDR/Closer com o historico atualizado

    Chame depois de `deal` estar com `id` preenchido (db.flush() apos o upsert).
    """
    if previous is not None:
        for field in TRACKED_FIELDS:
            old_value = previous.get(field)
            new_value = new_fields.get(field)
            if old_value != new_value:
                _record_event(db, deal, field, old_value, new_value, at)

    stage_changed = previous is not None and previous.get("stage_rd_id") != new_fields.get("stage_rd_id")
    owner_changed = (
        previous is not None
        and previous.get("current_owner_rd_id") != new_fields.get("current_owner_rd_id")
    )

    has_stage_history = (
        db.query(CrmDealStageHistory).filter(CrmDealStageHistory.deal_rd_id == deal.rd_id).first()
    )
    if (stage_changed or not has_stage_history) and new_fields.get("stage_rd_id"):
        if has_stage_history:
            _close_open_stage_history(db, deal.rd_id, at)
        db.add(
            CrmDealStageHistory(
                deal_id=deal.id,
                deal_rd_id=deal.rd_id,
                stage_rd_id=new_fields.get("stage_rd_id"),
                pipeline_rd_id=new_fields.get("pipeline_rd_id"),
                owner_rd_id=new_fields.get("current_owner_rd_id"),
                entered_at=at if has_stage_history else (new_fields.get("deal_created_at") or at),
            )
        )

    has_owner_history = (
        db.query(CrmDealOwnerHistory).filter(CrmDealOwnerHistory.deal_rd_id == deal.rd_id).first()
    )
    if (owner_changed or not has_owner_history) and new_fields.get("current_owner_rd_id"):
        if has_owner_history:
            _close_open_owner_history(db, deal.rd_id, at)
        db.add(
            CrmDealOwnerHistory(
                deal_id=deal.id,
                deal_rd_id=deal.rd_id,
                owner_rd_id=new_fields.get("current_owner_rd_id"),
                assigned_at=at if has_owner_history else (new_fields.get("deal_created_at") or at),
            )
        )

    db.flush()
    apply_sdr_closer_split(db, deal, deal.rd_id)
