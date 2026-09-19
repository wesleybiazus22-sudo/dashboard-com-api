"""Convite MANUAL do agente pra lead de prospecção ativa (fonte "Melhor Venda")
-- disparado pela SDR no dashboard (ver app/pages/7_🙋_Convite_Agente.py), não
automático como `webhooks/processor.py::_iniciar_atendimento_agente`.

Contexto: em prospecção ativa, a SDR pode oferecer ao contato uma demonstração
da conversa agêntica, pra causar mais impacto na venda. Um card de "Melhor
Venda" costuma puxar MAIS DE UM contato (RD não separa por pessoa dentro da
negociação) -- por isso o convite é sempre pra um `CrmContact` específico,
escolhido manualmente, nunca pra "a negociação" como um todo.

Depois desse envio, a conversa segue o fluxo normal de resposta: quando o lead
responder, `whatsapp/processor.py::_responder_com_agente` deixa o agente
continuar mesmo a negociação não sendo de tráfego pago, porque o telefone já
tem uma mensagem outbound registrada -- `_agente_ja_engajou` bypassa a trava de
origem (mesmo critério já usado pelos lembretes de reunião, que também mandam
template fora do gatilho automático)."""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from config.settings import settings
from database.models import CrmContact, CrmDeal, WhatsappMessage
from ingestion.whatsapp.client import WhatsappClient, normalizar_telefone_br

logger = logging.getLogger(__name__)


def _primeiro_nome(nome_completo: str | None, *, fallback: str) -> str:
    if nome_completo and nome_completo.strip():
        return nome_completo.strip().split()[0].capitalize()
    return fallback


def contatos_do_card(db: Session, deal: CrmDeal) -> list[CrmContact]:
    """Todos os contatos ligados ao card (não só o principal) -- o payload cru
    da negociação (`deal.raw`) guarda `contact_ids` como lista completa, mas
    `extract_deal_fields` só grava o primeiro em `contact_rd_id` (ver
    ingestion/rd_crm/deals.py). Contato ainda não sincronizado localmente
    (raro -- sync de contatos roda a cada 15 min) é omitido, não quebra."""
    contact_ids = (deal.raw or {}).get("contact_ids") or []
    if not contact_ids and deal.contact_rd_id:
        contact_ids = [deal.contact_rd_id]
    if not contact_ids:
        return []
    contatos = db.query(CrmContact).filter(CrmContact.rd_id.in_(contact_ids)).all()
    por_id = {c.rd_id: c for c in contatos}
    return [por_id[cid] for cid in contact_ids if cid in por_id]


def ja_convidados(db: Session, deal_rd_id: str) -> set[str]:
    """Telefones (já normalizados) que já receberam o convite manual nesse
    card -- pra UI não deixar a SDR reenviar pro mesmo contato."""
    linhas = db.query(WhatsappMessage.phone_number).filter(
        WhatsappMessage.deal_rd_id == deal_rd_id, WhatsappMessage.message_type == "template",
    ).all()
    return {telefone for (telefone,) in linhas}


def enviar_convite_manual(db: Session, *, deal: CrmDeal, contact: CrmContact, sdr_nome: str) -> tuple[bool, str]:
    """Manda o template de convite pro `contact` específico. Devolve
    (sucesso, mensagem) pra UI mostrar direto -- nunca levanta exceção (mesmo
    padrão de segurança de `_iniciar_atendimento_agente`: falha de rede/API
    vira mensagem de erro pra SDR ver na hora, não um crash da página)."""
    origens_permitidas = {
        s.strip() for s in settings.whatsapp_agent_melhor_venda_source_rd_ids.split(",") if s.strip()
    }
    if not deal.source or deal.source not in origens_permitidas:
        return False, "Essa negociação não é de origem Melhor Venda -- convite manual bloqueado."

    template = settings.whatsapp_agent_melhor_venda_template_name.strip()
    if not template:
        return False, "Template do convite manual ainda não configurado (aguardando aprovação no Meta Business Manager)."

    telefone = normalizar_telefone_br(contact.phone)
    if not telefone:
        return False, f"{contact.name or 'Contato'} não tem telefone válido cadastrado."

    if telefone in ja_convidados(db, deal.rd_id):
        return False, f"{contact.name or telefone} já recebeu esse convite antes."

    try:
        resposta = WhatsappClient().send_template(
            to=telefone,
            template_name=template,
            language_code=settings.whatsapp_agent_melhor_venda_template_language,
            components=[{
                "type": "body",
                "parameters": [
                    {"type": "text", "text": _primeiro_nome(contact.name, fallback="tudo bem")},
                    {"type": "text", "text": _primeiro_nome(sdr_nome, fallback="nossa equipe")},
                ],
            }],
        )
    except Exception:  # noqa: BLE001 -- vira mensagem de erro pra SDR, nunca derruba a pagina
        logger.exception("Convite manual: falha ao enviar pra %s (negociação %s).", telefone, deal.rd_id)
        return False, "Falha ao enviar a mensagem -- verifique os logs (pode ser template não aprovado ou número inválido)."

    wamid = (resposta.get("messages") or [{}])[0].get("id")
    db.add(WhatsappMessage(
        wamid=wamid or f"outbound-sem-id:{deal.rd_id}:{datetime.now(timezone.utc).timestamp()}",
        phone_number=telefone,
        direction="outbound",
        message_type="template",
        text_body=f"[template: {template} -- convite manual, SDR: {sdr_nome}]",
        contact_name=contact.name,
        deal_rd_id=deal.rd_id,
        raw=resposta,
        occurred_at=datetime.now(timezone.utc),
    ))
    db.commit()
    logger.info(
        "Convite manual: enviado pra %s (%s) -- negociação %s, SDR %s.",
        contact.name, telefone, deal.rd_id, sdr_nome,
    )
    return True, f"Convite enviado pra {contact.name or telefone} ({telefone})."
