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
from database.models import CrmContact, CrmDeal, WhatsappMessage
from ingestion.meta_ads.capi import MetaConversionsApiClient
from ingestion.rd_crm.deal_history import apply_sdr_closer_split, snapshot_deal, sync_deal_history
from ingestion.rd_crm.deals import extract_deal_fields
from ingestion.rd_crm.entities import upsert_by_rd_id
from ingestion.whatsapp.client import WhatsappClient, normalizar_telefone_br

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
            event_source_url=settings.meta_capi_event_source_url,
        )
        logger.info("CAPI: evento '%s' enviado pra negociacao %s.", settings.meta_capi_event_name, deal.rd_id)
    except Exception:  # noqa: BLE001 -- ver docstring: nunca deixa isso quebrar o webhook
        logger.exception("CAPI: falha ao enviar evento pra negociacao %s.", deal.rd_id)


def _primeiro_nome(nome_completo: str | None) -> str:
    """Primeiro token do nome, capitalizado -- pra variavel {{1}} do template.
    Fallback "tudo bem" (le natural em "Oi tudo bem!") quando nao ha nome."""
    if nome_completo and nome_completo.strip():
        return nome_completo.strip().split()[0].capitalize()
    return "tudo bem"


def _iniciar_atendimento_agente(db: Session, deal: CrmDeal) -> None:
    """Primeiro contato PROATIVO do agente: quando uma negociacao cai no CRM com
    uma origem configurada em WHATSAPP_AGENT_TRIGGER_SOURCE_RD_IDS OU um UTM
    medium configurado em WHATSAPP_AGENT_TRIGGER_UTM_MEDIUMS (ver
    config/settings.py -- default de ambos = "paid_social"), E ESTIVER na
    etapa WHATSAPP_AGENT_TRIGGER_STAGE_RD_ID (default = "Primeira Conexao"),
    manda a mensagem de ABERTURA pro lead, sem esperar ele escrever primeiro.

    source/utm_medium sao OU-logico entre si (basta um bater) porque o campo
    "Fonte" (source_id) e escolhido manualmente/automaticamente pelo RD e pode
    vir errado (ex: "Desconhecido") mesmo numa negociacao real de trafego
    pago -- confirmado 2026-09-19 com a negociacao "Click internet"
    (fbclid + utm_source=Instagram_Reels no custom_fields, mas source_id =
    "Desconhecido"), que por isso nunca recebeu a abertura. O utm_medium
    (tambem em custom_fields, ver ingestion/rd_crm/deals.py::
    _extract_utm_medium) costuma ser mais estavel pra esse caso.

    Ja a etapa e E-logico (trava adicional, nao alternativa) -- pedido
    explicito do usuario em 2026-09-19 pra NAO mandar a abertura automatica
    numa negociacao que ja saiu da coluna de entrada (ex: SDR ja fez contato
    manual por outro canal antes do sync rodar).

    So manda TEMPLATE (`WhatsappClient.send_template`), nunca texto livre --
    regra do proprio Meta: quem nunca mandou mensagem pro nosso numero so
    pode ser contatado via template pre-aprovado (fora da janela de 24h,
    texto livre e recusado). A CONVERSA em si (com o Claude, RAG, etc.) so
    comeca quando o lead RESPONDER (tocar no botao do template ou escrever) --
    isso e tratado em `whatsapp/processor.py::_responder_com_agente`.

    IDEMPOTENTE: pode ser chamada em qualquer webhook da negociacao (criacao ou
    update). So dispara o template UMA vez por negociacao -- as checagens
    abaixo (ja mandou template? o lead ja escreveu?) garantem isso, o que
    permite reagir tambem ao caso da origem `paid_social` chegar so num update
    posterior, sem risco de mandar a abertura duas vezes.

    Mesmo padrao de seguranca de `_notify_meta_capi`: efeito colateral do
    webhook, nunca a responsabilidade primaria -- qualquer falha (credencial
    ausente, template nao configurado, erro de rede, telefone invalido) so
    gera log e segue em frente."""
    origens_gatilho = {
        s.strip() for s in settings.whatsapp_agent_trigger_source_rd_ids.split(",") if s.strip()
    }
    utm_mediums_gatilho = {
        s.strip().lower() for s in settings.whatsapp_agent_trigger_utm_mediums.split(",") if s.strip()
    }
    bate_source = bool(deal.source) and deal.source in origens_gatilho
    bate_utm = bool(deal.utm_medium) and deal.utm_medium.strip().lower() in utm_mediums_gatilho
    if not bate_source and not bate_utm:
        return

    etapa_gatilho = settings.whatsapp_agent_trigger_stage_rd_id.strip()
    if etapa_gatilho and deal.stage_rd_id != etapa_gatilho:
        return

    if not settings.whatsapp_agent_template_name:
        logger.info(
            "Agente: negociacao %s tem origem gatilho, mas WHATSAPP_AGENT_TEMPLATE_NAME "
            "nao esta configurado -- pulando primeiro contato (configure o template no "
            "Meta Business Manager pra ativar).", deal.rd_id,
        )
        return

    # ja mandamos o template de abertura pra essa negociacao antes?
    if db.query(WhatsappMessage.id).filter(
        WhatsappMessage.deal_rd_id == deal.rd_id, WhatsappMessage.message_type == "template"
    ).first():
        return

    contact = None
    if deal.contact_rd_id:
        contact = db.query(CrmContact).filter(CrmContact.rd_id == deal.contact_rd_id).one_or_none()
    telefone = normalizar_telefone_br(contact.phone if contact else None)
    if not telefone:
        logger.info("Agente: negociacao %s sem telefone valido -- pulando primeiro contato.", deal.rd_id)
        return

    # o lead ja escreveu pra gente primeiro? entao a conversa ja abriu por conta
    # dele -- o fluxo de entrada cuida, nao manda a abertura por cima.
    if db.query(WhatsappMessage.id).filter(
        WhatsappMessage.phone_number == telefone, WhatsappMessage.direction == "inbound"
    ).first():
        return

    try:
        client = WhatsappClient()
        resposta = client.send_template(
            to=telefone,
            template_name=settings.whatsapp_agent_template_name,
            language_code=settings.whatsapp_agent_template_language,
            components=[{
                "type": "body",
                "parameters": [{"type": "text", "text": _primeiro_nome(contact.name if contact else None)}],
            }],
        )
        wamid = (resposta.get("messages") or [{}])[0].get("id")
        db.add(WhatsappMessage(
            wamid=wamid or f"outbound-sem-id:{deal.rd_id}:{_now().timestamp()}",
            phone_number=telefone,
            direction="outbound",
            message_type="template",
            text_body=f"[template: {settings.whatsapp_agent_template_name}]",
            contact_name=contact.name if contact else None,
            deal_rd_id=deal.rd_id,
            raw=resposta,
            occurred_at=_now(),
        ))
        db.commit()
        logger.info("Agente: primeiro contato enviado pra negociacao %s (telefone %s).", deal.rd_id, telefone)
    except Exception:  # noqa: BLE001 -- ver docstring: nunca deixa isso quebrar o webhook
        logger.exception("Agente: falha ao mandar primeiro contato pra negociacao %s.", deal.rd_id)


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

    # Primeiro contato do agente. Chamado em TODO webhook da negociacao (criacao
    # e update) porque a origem `paid_social` as vezes so aparece num update
    # posterior ao card ja existir -- a funcao e idempotente (so manda o
    # template uma vez por negociacao, ver as checagens la dentro).
    _iniciar_atendimento_agente(db, deal)
