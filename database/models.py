import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.connection import Base


def _uuid() -> str:
    return str(uuid.uuid4())


# ======================================================================
# OAUTH / SYNC CONTROL
# ======================================================================


class OAuthToken(Base):
    """Guarda o token vigente por produto RD (hoje só 'crm'). Sempre 1 linha por produto."""

    __tablename__ = "rd_oauth_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    product: Mapped[str] = mapped_column(String, unique=True, nullable=False)  # 'crm'
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )


class SyncState(Base):
    """Controle de sincronização incremental por entidade (deals, organizations, ...)."""

    __tablename__ = "sync_state"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    entity_name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_cursor: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )


class WebhookEvent(Base):
    """Log bruto de todo webhook recebido do RD CRM. transaction_uuid garante idempotência."""

    __tablename__ = "raw_crm_webhook_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    transaction_uuid: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)


# ======================================================================
# DIMENSÕES (core)
# ======================================================================


class CrmUser(Base):
    """Usuários do RD CRM = SDRs, closers, gestores."""

    __tablename__ = "crm_users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    rd_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    role: Mapped[str | None] = mapped_column(String, nullable=True)  # preenchido manualmente: sdr / closer / gestor
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CrmOrganization(Base):
    __tablename__ = "crm_organizations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    rd_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    segment: Mapped[str | None] = mapped_column(String, nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CrmContact(Base):
    __tablename__ = "crm_contacts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    rd_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    organization_rd_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CrmPipeline(Base):
    __tablename__ = "crm_pipelines"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    rd_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CrmStage(Base):
    __tablename__ = "crm_stages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    rd_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    pipeline_rd_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Mapeamento para o funil canônico (LEAD, SQL, OPPORTUNITY, DISCOVERY, PROPOSAL, NEGOTIATION, WON, LOST)
    canonical_stage: Mapped[str | None] = mapped_column(String, nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CrmLostReason(Base):
    __tablename__ = "crm_lost_reasons"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    rd_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


# ======================================================================
# FATOS (sales)
# ======================================================================


class CrmDeal(Base):
    __tablename__ = "crm_deals"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    rd_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)

    pipeline_rd_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    stage_rd_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True, index=True)  # open / won / lost

    organization_rd_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    contact_rd_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # Owner atual (o que o RD mostra hoje)
    current_owner_rd_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # Reconstruído via fact_owner_history / eventos de handoff (ver deal_owner_history)
    sdr_owner_rd_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    sdr_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closer_owner_rd_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    handoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    campaign: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)

    lost_reason_rd_id: Mapped[str | None] = mapped_column(String, nullable=True)

    deal_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deal_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_close_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    stage_history: Mapped[list["CrmDealStageHistory"]] = relationship(back_populates="deal")
    owner_history: Mapped[list["CrmDealOwnerHistory"]] = relationship(back_populates="deal")
    events: Mapped[list["CrmDealEvent"]] = relationship(back_populates="deal")


class CrmDealStageHistory(Base):
    """Uma linha por período em que a negociação ficou parada em uma etapa. Base do funil de velocity/aging."""

    __tablename__ = "crm_deal_stage_history"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(String, ForeignKey("crm_deals.id"), nullable=False, index=True)
    deal_rd_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    stage_rd_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Denormalizado (stage_id ja implica o pipeline) para facilitar queries de funil
    # sem precisar de join -- tambem torna visivel a troca de pipeline (handoff SDR->
    # closer nesta conta acontece movendo a mesma negociacao entre pipelines).
    pipeline_rd_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    owner_rd_id: Mapped[str | None] = mapped_column(String, nullable=True)  # dono no momento da etapa
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    deal: Mapped["CrmDeal"] = relationship(back_populates="stage_history")


class CrmDealOwnerHistory(Base):
    """Uma linha por período em que um usuário foi dono da negociação. Permite separar SDR de closer."""

    __tablename__ = "crm_deal_owner_history"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(String, ForeignKey("crm_deals.id"), nullable=False, index=True)
    deal_rd_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    owner_rd_id: Mapped[str | None] = mapped_column(String, nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    unassigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    deal: Mapped["CrmDeal"] = relationship(back_populates="owner_history")


class CrmDealEvent(Base):
    """Log genérico de qualquer mudança de campo relevante (auditoria + linha do tempo analítica)."""

    __tablename__ = "crm_deal_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    deal_id: Mapped[str] = mapped_column(String, ForeignKey("crm_deals.id"), nullable=False, index=True)
    deal_rd_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)  # stage_changed, owner_changed, status_changed...
    field_changed: Mapped[str | None] = mapped_column(String, nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    deal: Mapped["CrmDeal"] = relationship(back_populates="events")


class CrmTask(Base):
    __tablename__ = "crm_tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    rd_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    deal_rd_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    type: Mapped[str | None] = mapped_column(String, nullable=True)
    subject: Mapped[str | None] = mapped_column(String, nullable=True)
    owner_rd_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CrmMeeting(Base):
    __tablename__ = "crm_meetings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    rd_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    deal_rd_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    owner_rd_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True, index=True)  # scheduled / completed / no_show
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


__all__ = [
    "OAuthToken",
    "SyncState",
    "WebhookEvent",
    "CrmUser",
    "CrmOrganization",
    "CrmContact",
    "CrmPipeline",
    "CrmStage",
    "CrmLostReason",
    "CrmDeal",
    "CrmDealStageHistory",
    "CrmDealOwnerHistory",
    "CrmDealEvent",
    "CrmTask",
    "CrmMeeting",
]
