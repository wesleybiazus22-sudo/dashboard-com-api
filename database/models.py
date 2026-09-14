import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
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


class WhatsappWebhookEvent(Base):
    """Log bruto de todo webhook recebido do WhatsApp Cloud API. Mesmo papel de
    `WebhookEvent`, mas separado porque a chave de idempotencia e diferente: o
    WhatsApp nao manda um `transaction_uuid` por evento -- usamos um hash do
    corpo inteiro do payload como chave (ver `whatsapp/processor.py`), porque um
    unico POST pode trazer varias mensagens/status de uma vez (nao ha 1 id
    natural por request)."""

    __tablename__ = "raw_whatsapp_webhook_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    dedupe_key: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class WhatsappMessage(Base):
    """Uma linha por mensagem trocada no WhatsApp (recebida OU enviada) -- historico
    de conversa por contato. Serve pra auditoria (o que o agente disse/fez) e,
    mais pra frente, pra dar contexto de conversa ao motor de IA (ver
    ingestion/rd_crm/actions.py e a discussao do agente de atendimento).

    `wamid` e o id nativo do WhatsApp pra essa mensagem especifica -- garante
    idempotencia por mensagem (diferente de `WhatsappWebhookEvent.dedupe_key`,
    que e por REQUEST inteira, que pode conter varias mensagens)."""

    __tablename__ = "whatsapp_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    wamid: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    phone_number: Mapped[str] = mapped_column(String, nullable=False, index=True)  # numero do CONTATO (lead), sempre -- quem enviou/recebeu vem de `direction`
    direction: Mapped[str] = mapped_column(String, nullable=False)  # "inbound" | "outbound"
    message_type: Mapped[str] = mapped_column(String, nullable=False)  # text, image, template, button, ...
    text_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String, nullable=True)  # nome de perfil do WhatsApp, quando vem no payload
    deal_rd_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)  # vinculo com o CRM -- preenchido quando o contato e identificado/criado
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class LlmCallLog(Base):
    """Uma linha por chamada ao modelo de IA (Claude) feita pelo agente de
    atendimento -- registra tokens e custo calculado, pra acompanhar gasto de
    LLM no dashboard. Gravado pelo proprio agente a cada resposta gerada (ver
    modulo do agente, ainda a construir) -- essa tabela nasce ANTES do agente
    de proposito, pra o custo ja vir monitorado desde a primeira chamada real,
    em vez de precisar ser adicionado depois por cima.

    `custo_usd` fica congelado no valor calculado NA HORA da chamada (preco do
    modelo x tokens) -- nao e recalculado depois, entao uma mudanca futura de
    preco da Anthropic nao reescreve o historico."""

    __tablename__ = "llm_call_log"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    model: Mapped[str] = mapped_column(String, nullable=False, index=True)  # ex: "claude-sonnet-5"
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    # Tokens de cache (leitura/escrita) ficam separados pq tem preco DIFERENTE
    # do token normal de input -- somar tudo junto na mesma coluna inflaria o
    # custo calculado se um dia usarmos prompt caching no agente.
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    custo_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False)
    finalidade: Mapped[str | None] = mapped_column(String, nullable=True)  # ex: "resposta_lead", "qualificacao"
    phone_number: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    deal_rd_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)


class WhatsappConversationCost(Base):
    """Custo REAL de mensageria cobrado pelo Meta, sincronizado direto da API
    de analytics de conversas do WABA (nao e um calculo nosso -- e o numero
    oficial que o Meta fatura, evita reconstruir a logica de janela de 24h/
    categoria de conversa por conta propria, que tem detalhes proprios e muda
    de vez em quando). Grao: (dia, categoria, tipo, numero de telefone)."""

    __tablename__ = "whatsapp_conversation_cost"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    conversation_category: Mapped[str] = mapped_column(String, nullable=False)  # MARKETING, UTILITY, AUTHENTICATION, SERVICE
    conversation_type: Mapped[str] = mapped_column(String, nullable=False)  # REGULAR, FREE_TIER, FREE_ENTRY_POINT
    phone_number: Mapped[str | None] = mapped_column(String, nullable=True)
    conversation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class KnowledgeChunk(Base):
    """Um pedaco da base de conhecimento que o agente consulta (RAG) pra
    responder duvida sobre a solucao -- extraido dos materiais reais da
    empresa (landing page oficial, hoje; outros documentos depois).

    Retrieval por BUSCA TEXTUAL do Postgres (`to_tsvector`/`ts_rank`), nao por
    embedding/busca semantica com vetor -- decisao deliberada: a base hoje tem
    poucas dezenas de pedacos (cabe inteira em texto, sem precisar de vetor
    pra achar o relevante), e assim evita depender de mais uma API externa
    (embeddings) so pra isso. Se a base crescer muito (centenas de paginas,
    perguntas muito parafraseadas que a busca textual comece a nao achar),
    migrar pra pgvector e um upgrade localizado -- so troca `buscar_contexto`
    em ingestion/llm/retrieval.py, o resto do agente nao muda.

    `titulo` funciona como chave estavel pra upsert (ver
    scripts/load_knowledge_base.py) -- recarregar o conteudo atualizado no
    mesmo titulo substitui a versao antiga, em vez de duplicar."""

    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    titulo: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    categoria: Mapped[str] = mapped_column(String, nullable=False, index=True)  # produto, agente, objecao, processo, ...
    conteudo: Mapped[str] = mapped_column(Text, nullable=False)
    fonte: Mapped[str | None] = mapped_column(String, nullable=True)  # de onde veio (ex: nome do arquivo original)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


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
    name: Mapped[str | None] = mapped_column(String, nullable=True)  # = custom_fields['nome-fantasia']
    # custom_fields['razao-social'] -- essencial pro cruzamento com o Melhor Venda,
    # que exporta razao social, nao nome fantasia (que e o "name" acima).
    legal_name: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
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
    # Agrupa pipelines do mesmo produto (ex: "[Máquina ISP] - Qualificação" e
    # "[Máquina ISP] Closer" viram um funil so). Preenchido por
    # ingestion/canonical_funnel.py, nao pela API do RD.
    product_group: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
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


class CrmDealSource(Base):
    """Origem da negociacao (ex: "Melhor Venda", "Feiras e Eventos", "Prospeccao
    Ativa"). No RD isso vem so como um id dentro da negociacao (`source_id`) -- sem
    esta tabela de lookup o dashboard nao consegue mostrar/filtrar por origem, que e
    uma das dimensoes analiticas mais importantes do funil."""

    __tablename__ = "crm_deal_sources"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    rd_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CrmCampaign(Base):
    """Campanha de marketing vinculada a negociacao (`campaign_id` no RD). Mesma
    logica de CrmDealSource: sem o lookup, so temos ids opacos."""

    __tablename__ = "crm_campaigns"

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
    status: Mapped[str | None] = mapped_column(String, nullable=True, index=True)  # ongoing / won / lost

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

    # Click ID do Meta capturado no RD Marketing e copiado pro card da negociacao
    # via campo personalizado (ver README secao 14) -- fecha o loop entre o clique
    # no anuncio e o avanco no funil de vendas (ver ingestion/meta_ads/capi.py e o
    # gatilho em webhooks/processor.py). NULL pra maioria das negociacoes: so
    # existe quando (a) o lead veio de um clique em anuncio do Meta E (b) o campo
    # personalizado ja estava configurado no RD quando a negociacao foi criada.
    fbclid: Mapped[str | None] = mapped_column(String, nullable=True)

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


class AgendaEventoMicrosoft(Base):
    """1 linha por negociacao com evento criado no Microsoft Graph por
    `_criar_evento_na_agenda` (ver ingestion/llm/agent.py) -- guarda o ID do
    evento pra `reagendar_reuniao` conseguir mover o evento DE VERDADE no
    Outlook/Teams quando o lead pede pra remarcar, em vez de so avisar um
    humano pra ajustar manualmente. Sem essa tabela nao ha como reencontrar
    o evento depois de criado (a API do Graph nao devolve isso pelo
    deal_rd_id, so pelo proprio ID que ela gerou na criacao)."""

    __tablename__ = "agenda_eventos_microsoft"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    deal_rd_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    email_organizador: Mapped[str] = mapped_column(String, nullable=False)
    evento_id: Mapped[str] = mapped_column(String, nullable=False)
    web_link: Mapped[str | None] = mapped_column(String, nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class AgenteLembreteReuniao(Base):
    """Controla os 3 lembretes automaticos (vespera 20h, manha 08h, 1h antes)
    de uma reuniao marcada (`crm_tasks` tipo 'meeting') no pipeline [Máquina
    ISP] - Qualificação -- ver scripts/enviar_lembretes_reuniao.py. 1 linha
    por (negociacao, horario da reuniao): se a reuniao for reagendada
    (`crm_tasks.due_at` muda), uma linha NOVA e criada pro novo horario e os
    lembretes recomecam do zero pra ele -- a linha antiga fica orfa e
    inofensiva, nunca e apagada (historico)."""

    __tablename__ = "agente_lembretes_reuniao"
    __table_args__ = (UniqueConstraint("deal_rd_id", "reuniao_due_at", name="uq_lembrete_deal_horario"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    deal_rd_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    reuniao_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    vespera_enviado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    manha_enviado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    uma_hora_antes_enviado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    no_show_marcado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


# ======================================================================
# MELHOR VENDA (outbound) -- reconciliacao manual/semi-automatica com o CRM
# ======================================================================


class MvCampaign(Base):
    """Uma campanha semanal do Melhor Venda (ex: lista de ~50 empresas prospectadas
    naquela semana). Nao vem de API nenhuma -- criada a partir do print que o
    usuario manda toda semana."""

    __tablename__ = "mv_campaigns"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    sdr_name: Mapped[str | None] = mapped_column(String, nullable=True)  # nome da SDR responsavel, ex: "Miriã"
    label: Mapped[str | None] = mapped_column(String, nullable=True)  # ex: "Agosto/Semana 1"
    week_start: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    week_end: Mapped[datetime] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    companies: Mapped[list["MvCampaignCompany"]] = relationship(back_populates="campaign")


class MvCampaignCompany(Base):
    """Uma empresa dentro de uma campanha do Melhor Venda, com o resultado do
    cruzamento contra o CRM (quando encontrado)."""

    __tablename__ = "mv_campaign_companies"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    campaign_id: Mapped[str] = mapped_column(String, ForeignKey("mv_campaigns.id"), nullable=False, index=True)

    company_name_mv: Mapped[str] = mapped_column(String, nullable=False)  # nome como aparece no MV
    cnpj_mv: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    mv_status: Mapped[str | None] = mapped_column(String, nullable=True)  # status dentro do MV (Conectado/Sem Retorno)

    # CONFIRMADO -- so preenchido por CNPJ (inequivoco) ou confirmacao manual. Isso e
    # o que as views/relatorios devem usar.
    matched_organization_rd_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    matched_deal_rd_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # 'auto_cnpj' | 'manual'
    match_confidence: Mapped[str | None] = mapped_column(String, nullable=True)

    # SUGESTAO por similaridade de nome -- NUNCA promovido a matched_* sozinho (nomes
    # de ISP/telecom compartilham demais palavras genericas pra confiar sem revisao).
    # Precisa de confirm_suggestion() explicito.
    suggested_deal_rd_id: Mapped[str | None] = mapped_column(String, nullable=True)
    suggested_organization_rd_id: Mapped[str | None] = mapped_column(String, nullable=True)
    suggested_score: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    campaign: Mapped["MvCampaign"] = relationship(back_populates="companies")


# ======================================================================
# META ADS (Marketing API) -- performance de campanhas de trafego pago
# ======================================================================


class MetaCampaign(Base):
    """Campanha do Meta Ads (Facebook/Instagram). `meta_id` e o id nativo do Meta,
    equivalente ao `rd_id` das entidades do RD -- chave natural usada no upsert."""

    __tablename__ = "meta_campaigns"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    meta_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    objective: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    effective_status: Mapped[str | None] = mapped_column(String, nullable=True)
    daily_budget: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    lifetime_budget: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stop_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class MetaAdSet(Base):
    """Conjunto de anuncios (nivel de segmentacao/publico dentro de uma campanha)."""

    __tablename__ = "meta_adsets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    meta_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    campaign_meta_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    effective_status: Mapped[str | None] = mapped_column(String, nullable=True)
    daily_budget: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    lifetime_budget: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class MetaAd(Base):
    """Anuncio individual (nivel de criativo)."""

    __tablename__ = "meta_ads"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    meta_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    adset_meta_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    campaign_meta_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    effective_status: Mapped[str | None] = mapped_column(String, nullable=True)
    creative_thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class MetaInsightDaily(Base):
    """Uma linha por (anuncio, dia) -- metrica de performance vinda do endpoint
    /insights com level=ad e time_increment=1. Consultar no nivel de anuncio (em vez
    de fazer 3 chamadas separadas por campanha/conjunto/anuncio) porque a resposta ja
    vem com campaign_id/adset_id/ad_id juntos na mesma linha, permitindo agregar pra
    qualquer nivel a partir de uma unica sincronizacao.

    Chave natural = (ad_meta_id, date) -- upsert por composicao, nao por id unico
    (ver `upsert_insight_row` em ingestion/meta_ads/entities.py).
    """

    __tablename__ = "meta_insights_daily"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)

    campaign_meta_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    campaign_name: Mapped[str | None] = mapped_column(String, nullable=True)
    adset_meta_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    adset_name: Mapped[str | None] = mapped_column(String, nullable=True)
    ad_meta_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    ad_name: Mapped[str | None] = mapped_column(String, nullable=True)

    spend: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    impressions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reach: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frequency: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    ctr: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    cpc: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)
    cpm: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)

    # Bruto de proposito: o Meta tem dezenas de action_type (link_click, lead,
    # purchase, add_to_cart, ...) e a taxonomia varia por objetivo de campanha. Guardar
    # a lista crua permite as views extrairem o que interessa (ex: "lead") sem que o
    # ingestor precise conhecer de antemao todo tipo de acao possivel.
    # none_as_null=True: sem isso, atribuir Python None a uma coluna JSON(B) grava o
    # LITERAL JSON `null` (um valor jsonb valido) em vez de NULL de banco -- e
    # `jsonb_array_elements` quebra com "cannot extract elements from a scalar" ao
    # tentar iterar sobre esse `null` (ver comentario na view v_meta_insights_enriched,
    # que tambem se defende disso pros dados ja gravados antes desta correcao).
    actions: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    cost_per_action_type: Mapped[list | None] = mapped_column(JSONB(none_as_null=True), nullable=True)

    # Engajamento de video -- campos NOMEADOS da API (nao entram no array generico
    # `actions`, precisam ser pedidos explicitamente no `fields` do /insights). NULL
    # de verdade (nao 0) quando o anuncio nao e de video -- 0 significa "e video, mas
    # ninguem chegou nesse marco". Ja vem somados em `parse_action_sum` na ingestao
    # (ver ingestion/meta_ads/entities.py) em vez de guardados como JSONB bruto: ao
    # contrario de `actions`, sao so 2 metricas fixas e conhecidas, sem taxonomia
    # variavel -- nao ha ganho em manter o JSON cru.
    video_thruplay: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_view_50: Mapped[int | None] = mapped_column(Integer, nullable=True)

    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


# ======================================================================
# GOOGLE ANALYTICS 4 (Data API) -- comportamento de trafego no site
# ======================================================================
#
# Ao contrario do RD/Meta, o GA4 nao expoe entidades com id proprio: cada
# relatorio ja vem agregado por uma combinacao de dimensoes (ver
# ingestion/ga4/client.py). Por isso cada tabela abaixo tem uma chave natural
# COMPOSTA diferente (sempre incluindo `date`), sem equivalente a `meta_id`/`rd_id`
# -- upsert generico em `upsert_by_composite` (ingestion/ga4/entities.py).


class Ga4DailyOverview(Base):
    """Uma linha por dia: metricas-resumo do site inteiro (visao geral)."""

    __tablename__ = "ga4_daily_overview"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    date: Mapped[datetime] = mapped_column(Date, unique=True, nullable=False, index=True)

    sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_users: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_users: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engaged_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engagement_rate: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    avg_session_duration: Mapped[float | None] = mapped_column(Numeric(14, 4), nullable=True)  # segundos
    bounce_rate: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    screen_page_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    key_events: Mapped[int | None] = mapped_column(Integer, nullable=True)  # "conversoes" no nome antigo da UI

    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Ga4TrafficSourceDaily(Base):
    """Uma linha por (dia, canal, origem, midia) -- de onde vem o trafego
    (organico, pago, social, direto, referencia, ...)."""

    __tablename__ = "ga4_traffic_source_daily"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    channel_group: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    medium: Mapped[str] = mapped_column(String, nullable=False)

    sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_users: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engaged_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    key_events: Mapped[int | None] = mapped_column(Integer, nullable=True)

    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Ga4UtmCampaignDaily(Base):
    """Uma linha por (dia, canal, origem, midia, utm_campaign, utm_content) --
    quebra o trafego pelas TAGS DE UTM que a propria operacao coloca nos links dos
    anuncios (`utm_campaign`/`utm_content`), em vez de so pela deteccao automatica
    do GA4 (`channel_group`/`source`/`medium`, que existe em Ga4TrafficSourceDaily).
    Como a mesma pessoa controla o nome da UTM, essa quebra fica mais precisa que a
    automatica pra casar trafego com o nome exato da campanha usado no Meta Ads.

    `utm_campaign`/`utm_content` vem das dimensoes `sessionManualCampaignName`/
    `sessionManualAdContent` do GA4 -- "manual" no nome delas significa "populado
    por UTM explicita na URL", diferente de `sessionCampaignName` (que tambem
    preenche via auto-tagging do Google Ads, sem UTM nenhuma)."""

    __tablename__ = "ga4_utm_campaign_daily"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    channel_group: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    medium: Mapped[str] = mapped_column(String, nullable=False)
    utm_campaign: Mapped[str] = mapped_column(Text, nullable=False)
    utm_content: Mapped[str] = mapped_column(Text, nullable=False)

    sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_users: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engaged_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    key_events: Mapped[int | None] = mapped_column(Integer, nullable=True)

    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Ga4PageDaily(Base):
    """Uma linha por (dia, pagina) -- quais paginas do site recebem mais trafego."""

    __tablename__ = "ga4_page_daily"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    page_path: Mapped[str] = mapped_column(Text, nullable=False)
    page_title: Mapped[str] = mapped_column(Text, nullable=False)

    screen_page_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_users: Mapped[int | None] = mapped_column(Integer, nullable=True)

    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Ga4DeviceDaily(Base):
    """Uma linha por (dia, categoria de dispositivo, navegador)."""

    __tablename__ = "ga4_device_daily"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    device_category: Mapped[str] = mapped_column(String, nullable=False)  # desktop / mobile / tablet
    browser: Mapped[str] = mapped_column(String, nullable=False)

    sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_users: Mapped[int | None] = mapped_column(Integer, nullable=True)

    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Ga4GeoDaily(Base):
    """Uma linha por (dia, pais, cidade) -- de onde geograficamente vem o trafego."""

    __tablename__ = "ga4_geo_daily"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    country: Mapped[str] = mapped_column(String, nullable=False)
    city: Mapped[str] = mapped_column(String, nullable=False)

    sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_users: Mapped[int | None] = mapped_column(Integer, nullable=True)

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
    "CrmDealSource",
    "CrmCampaign",
    "CrmDeal",
    "CrmDealStageHistory",
    "CrmDealOwnerHistory",
    "CrmDealEvent",
    "CrmTask",
    "CrmMeeting",
    "MvCampaign",
    "MvCampaignCompany",
    "MetaCampaign",
    "MetaAdSet",
    "MetaAd",
    "MetaInsightDaily",
    "Ga4DailyOverview",
    "Ga4TrafficSourceDaily",
    "Ga4UtmCampaignDaily",
    "Ga4PageDaily",
    "Ga4DeviceDaily",
    "Ga4GeoDaily",
    "WhatsappWebhookEvent",
    "WhatsappMessage",
    "LlmCallLog",
    "WhatsappConversationCost",
    "KnowledgeChunk",
]
