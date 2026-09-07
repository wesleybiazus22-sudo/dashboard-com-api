"""
Traduz o payload cru do webhook do WhatsApp Cloud API em linhas de
`whatsapp_messages` -- mesmo papel de `webhooks/processor.py` pro RD CRM, so que
pro WhatsApp.

Formato do payload (documentado e estavel na API do Meta, ao contrario do RD):
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "<WABA_ID>",
    "changes": [{
      "field": "messages",
      "value": {
        "messaging_product": "whatsapp",
        "metadata": {"phone_number_id": "..."},
        "contacts": [{"profile": {"name": "..."}, "wa_id": "..."}],
        "messages": [{"from": "...", "id": "wamid...", "timestamp": "...", "type": "text", "text": {"body": "..."}}],
        # OU, em vez de "messages", pode vir "statuses" (confirmacao de entrega/
        # leitura de mensagem que NOS enviamos) -- ignoramos essas por enquanto,
        # nao sao mensagem nova de lead.
      }
    }]
  }]
}

O agente (Claude + RAG + ferramentas de CRM, ver ingestion/llm/agent.py) entra
em `_responder_com_agente`, chamado no fim de `processar_evento` pra toda
mensagem de TEXTO recebida. Segue o mesmo padrao de seguranca ja usado em
`webhooks/processor.py` (CAPI, primeiro contato): e um efeito colateral do
recebimento da mensagem, nunca pode derrubar o proprio recebimento -- qualquer
falha (credencial ausente, erro do Claude, erro de rede do WhatsApp) so gera
log, nunca uma excecao que propaga.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from database.models import CrmContact, CrmDeal, WhatsappMessage
from ingestion.llm.agent import conversar
from ingestion.whatsapp.client import WhatsappClient, normalizar_telefone_br

logger = logging.getLogger(__name__)


def _parse_timestamp(valor) -> datetime:
    """O WhatsApp manda `timestamp` sempre como epoch em SEGUNDOS (string) --
    formato fixo e documentado, diferente do RD (que mistura formatos). Cai pro
    instante atual se vier vazio/invalido, pra nunca falhar a gravacao da
    mensagem por causa so do carimbo de hora."""
    try:
        return datetime.fromtimestamp(int(valor), tz=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def _upsert_mensagem(db: Session, *, wamid: str, phone_number: str, direction: str,
                      message_type: str, text_body: str | None, contact_name: str | None,
                      occurred_at, raw: dict) -> WhatsappMessage:
    """Idempotente por `wamid` -- o Meta pode reenviar o mesmo webhook mais de
    uma vez (retry por timeout, por exemplo); sem isso a mensagem apareceria
    duplicada no historico."""
    obj = db.query(WhatsappMessage).filter(WhatsappMessage.wamid == wamid).one_or_none()
    if obj is None:
        obj = WhatsappMessage(wamid=wamid)
        db.add(obj)
    obj.phone_number = phone_number
    obj.direction = direction
    obj.message_type = message_type
    obj.text_body = text_body
    obj.contact_name = contact_name
    obj.occurred_at = occurred_at
    obj.raw = raw
    db.flush()
    return obj


def _extrai_texto(mensagem: dict) -> str | None:
    """So mensagens de texto tem corpo direto -- outros tipos (imagem, audio,
    botao, localizacao) tem estrutura propria. Por enquanto so extraimos texto
    (e a legenda de botao/lista, que e o mais proximo de "texto" que tipos
    interativos tem); mensagem de midia fica registrada com texto vazio, mas o
    `raw` completo continua salvo pra tratar depois se precisar."""
    tipo = mensagem.get("type")
    if tipo == "text":
        return (mensagem.get("text") or {}).get("body")
    if tipo == "button":
        return (mensagem.get("button") or {}).get("text")
    if tipo == "interactive":
        interativo = mensagem.get("interactive") or {}
        resposta = interativo.get("button_reply") or interativo.get("list_reply") or {}
        return resposta.get("title")
    return None


def _carregar_historico(db: Session, phone_number: str, *, exceto_wamid: str, limite: int = 20) -> list[dict]:
    """Reconstroi um historico simplificado (so texto) das ultimas mensagens
    trocadas com esse numero, pra dar contexto ao agente entre turnos --
    NAO inclui a mensagem atual (`exceto_wamid`), que `conversar()` ja
    adiciona sozinha. Mensagem sem texto (midia, por exemplo) e pulada --
    perder uma imagem do contexto e aceitavel, quebrar o formato esperado
    pela API (que exige conteudo) nao."""
    linhas = (
        db.query(WhatsappMessage)
        .filter(WhatsappMessage.phone_number == phone_number, WhatsappMessage.wamid != exceto_wamid)
        .order_by(WhatsappMessage.occurred_at.desc())
        .limit(limite)
        .all()
    )
    historico = []
    for linha in reversed(linhas):
        if not linha.text_body:
            continue
        papel = "user" if linha.direction == "inbound" else "assistant"
        historico.append({"role": papel, "content": [{"type": "text", "text": linha.text_body}]})
    return historico


def _deal_rd_id_por_telefone(db: Session, phone_number: str) -> str | None:
    """Tenta achar uma negociacao existente ligada a esse telefone, pra o
    agente poder AGIR no CRM de verdade (mover etapa, criar tarefa) em vez de
    so simular. Melhor esforco: casa o telefone normalizado do contato do RD
    contra o numero que mandou a mensagem (o formato salvo no RD e
    inconsistente -- ver `normalizar_telefone_br`); se achar mais de uma
    negociacao pro mesmo contato, fica com a mais recente.

    Faz um scan simples em Python (nao um WHERE normalizado no SQL) -- aceitavel
    pro volume atual de contatos; se a base crescer muito, vale mover a
    normalizacao pra uma coluna indexada em vez de comparar em memoria."""
    candidatos = db.query(CrmContact).filter(CrmContact.phone.isnot(None)).all()
    contato = next((c for c in candidatos if normalizar_telefone_br(c.phone) == phone_number), None)
    if not contato:
        return None
    deal = (
        db.query(CrmDeal)
        .filter(CrmDeal.contact_rd_id == contato.rd_id)
        .order_by(CrmDeal.deal_created_at.desc())
        .first()
    )
    return deal.rd_id if deal else None


def _responder_com_agente(db: Session, *, phone_number: str, texto: str, wamid_recebido: str, contact_name: str | None) -> None:
    """Chama o agente e manda a resposta de volta pro lead pelo WhatsApp de
    verdade (`send_text` -- valido aqui porque o LEAD acabou de mandar
    mensagem, o que abre a janela de 24h de texto livre; nao precisa de
    template pra RESPONDER, so pra iniciar contato -- ver
    webhooks/processor.py::_iniciar_atendimento_agente)."""
    try:
        historico = _carregar_historico(db, phone_number, exceto_wamid=wamid_recebido)
        deal_rd_id = _deal_rd_id_por_telefone(db, phone_number)
        resposta, _ = conversar(
            db, historico, texto, telefone=phone_number, deal_rd_id=deal_rd_id, modo_teste=False,
        )
        if not resposta:
            logger.warning("Agente: resposta vazia pro numero %s -- nada enviado.", phone_number)
            return

        envio = WhatsappClient().send_text(to=phone_number, body=resposta)
        wamid_saida = (envio.get("messages") or [{}])[0].get("id")
        _upsert_mensagem(
            db,
            wamid=wamid_saida or f"outbound-sem-id:{phone_number}:{datetime.now(timezone.utc).timestamp()}",
            phone_number=phone_number,
            direction="outbound",
            message_type="text",
            text_body=resposta,
            contact_name=contact_name,
            occurred_at=datetime.now(timezone.utc),
            raw=envio,
        )
        db.commit()
        logger.info("Agente: respondeu pro numero %s (negociacao vinculada: %s).", phone_number, deal_rd_id or "nenhuma")
    except Exception:  # noqa: BLE001 -- ver docstring do modulo: nunca derruba o recebimento do webhook
        logger.exception("Agente: falha ao responder pro numero %s.", phone_number)


def processar_evento(db: Session, payload: dict) -> int:
    """Processa UM payload de webhook (pode conter varias mensagens/eventos
    dentro). Devolve quantas mensagens novas foram gravadas."""
    count = 0
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change.get("value", {})

            contatos_por_wa_id = {
                c.get("wa_id"): (c.get("profile") or {}).get("name")
                for c in value.get("contacts", [])
            }

            for mensagem in value.get("messages", []):
                wamid = mensagem.get("id")
                numero = mensagem.get("from")
                if not wamid or not numero:
                    continue

                _upsert_mensagem(
                    db,
                    wamid=wamid,
                    phone_number=numero,
                    direction="inbound",
                    message_type=mensagem.get("type", "unknown"),
                    text_body=_extrai_texto(mensagem),
                    contact_name=contatos_por_wa_id.get(numero),
                    occurred_at=_parse_timestamp(mensagem.get("timestamp")),
                    raw=mensagem,
                )
                count += 1
                logger.info("WhatsApp: mensagem recebida de %s (wamid=%s, tipo=%s)", numero, wamid, mensagem.get("type"))

                texto = _extrai_texto(mensagem)
                if texto:
                    _responder_com_agente(
                        db, phone_number=numero, texto=texto, wamid_recebido=wamid,
                        contact_name=contatos_por_wa_id.get(numero),
                    )

            # "statuses" (entrega/leitura de mensagem NOSSA) chega no mesmo
            # payload as vezes -- nao e mensagem de lead, so log por enquanto.
            for status in value.get("statuses", []):
                logger.debug("WhatsApp: status '%s' pra wamid=%s", status.get("status"), status.get("id"))

    db.commit()
    return count
