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

from config.settings import settings
from database.models import CrmContact, CrmDeal, CrmDealSource, WhatsappMessage
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


def _chave_telefone(numero: str | None) -> str | None:
    """Nucleo comparavel do telefone: DDD + 8 digitos finais, SEM codigo de
    pais e SEM o 9 extra do celular brasileiro. O RD salva com o 9
    (`5585981043020`), o WhatsApp as vezes manda sem (`558598104302`... ou o
    caso real do teste: `555496123100`) -- comparar pelo nucleo faz os dois
    baterem. Ex: `+5585 9 8104-3020` e `5585 8104-3020` -> ambos `8598104302`."""
    if not numero:
        return None
    d = "".join(ch for ch in numero if ch.isdigit())
    if d.startswith("55") and len(d) >= 12:
        d = d[2:]  # tira o codigo do pais
    if len(d) == 11:  # DDD + 9 + 8 digitos -> tira o 9
        d = d[:2] + d[3:]
    return d[-10:] if len(d) >= 10 else d


def _deal_por_telefone(db: Session, phone_number: str) -> CrmDeal | None:
    """Tenta achar uma negociacao existente ligada a esse telefone, pra o
    agente poder AGIR no CRM de verdade (mover etapa, criar tarefa) em vez de
    so simular. Casa pelo NUCLEO do numero (ver `_chave_telefone`) -- tolerante
    ao 9 extra do celular e ao codigo de pais, porque o formato salvo no RD e o
    que o WhatsApp manda nem sempre batem digito a digito. Se achar mais de uma
    negociacao pro mesmo contato, fica com a mais recente.

    Faz um scan simples em Python (nao um WHERE normalizado no SQL) -- aceitavel
    pro volume atual de contatos; se a base crescer muito, vale mover a chave
    pra uma coluna indexada em vez de comparar em memoria."""
    alvo = _chave_telefone(phone_number)
    if not alvo:
        return None
    candidatos = db.query(CrmContact).filter(CrmContact.phone.isnot(None)).all()
    contato = next((c for c in candidatos if _chave_telefone(c.phone) == alvo), None)
    if not contato:
        return None
    return (
        db.query(CrmDeal)
        .filter(CrmDeal.contact_rd_id == contato.rd_id)
        .order_by(CrmDeal.deal_created_at.desc())
        .first()
    )


def _pode_responder_automaticamente(phone_number: str) -> bool:
    """Trava de piloto controlado -- ver WHATSAPP_AGENT_RESTRICT_TO_PHONE_NUMBERS
    em config/settings.py. Vazio = sem restricao por numero (default);
    preenchido = so responde quem estiver na lista (usado no piloto)."""
    numeros_liberados = {
        n.strip() for n in settings.whatsapp_agent_restrict_to_phone_numbers.split(",") if n.strip()
    }
    if not numeros_liberados:
        return True
    return phone_number in numeros_liberados


def _e_numero_de_teste(phone_number: str) -> bool:
    """Numero na lista de teste (WHATSAPP_AGENT_TEST_PHONE_NUMBERS) -- passa
    direto pelas travas de origem e de etapa. Compara pelo nucleo do numero."""
    alvo = _chave_telefone(phone_number)
    if not alvo:
        return False
    return any(
        _chave_telefone(n.strip()) == alvo
        for n in settings.whatsapp_agent_test_phone_numbers.split(",")
        if n.strip()
    )


def _lead_de_trafego_pago(db: Session, deal: CrmDeal | None) -> bool:
    """Trava POR ORIGEM (ver WHATSAPP_AGENT_PAID_TRAFFIC_MARKER). O agente so
    responde lead de trafego pago -- checado por DOIS sinais na negociacao:
    (a) o nome da origem (crm_deal_sources.name) contem a marca, OU
    (b) o `utm_medium` gravado no card contem a marca.
    Sem negociacao ainda (card nao sincronizado) => False aqui, mas o
    reprocessamento (scripts/reprocessar_whatsapp_pendentes.py) tenta de novo
    depois que o card aparecer."""
    marca = settings.whatsapp_agent_paid_traffic_marker.strip().lower()
    if not marca:
        return True  # trava desligada
    if deal is None:
        return False

    utm_medium = ((deal.raw or {}).get("custom_fields") or {}).get("utm_medium") or ""
    if marca in utm_medium.lower():
        return True

    if deal.source:
        origem = db.query(CrmDealSource).filter(CrmDealSource.rd_id == deal.source).one_or_none()
        if origem and origem.name and marca in origem.name.lower():
            return True

    return False


def _agente_ja_engajou(db: Session, phone_number: str) -> bool:
    """True se o agente ja mandou pelo menos uma mensagem pra esse telefone --
    ou seja, a conversa ja esta em andamento (nao e mais primeiro contato)."""
    return (
        db.query(WhatsappMessage.id)
        .filter(WhatsappMessage.phone_number == phone_number, WhatsappMessage.direction == "outbound")
        .first()
        is not None
    )


def _pode_iniciar_atendimento(db: Session, phone_number: str, deal: CrmDeal | None) -> bool:
    """So deixa o agente INICIAR o atendimento (primeira resposta) se a
    negociacao estiver na etapa "Primeira Conexao" -- ver
    RD_STAGE_PRIMEIRA_CONEXAO_RD_ID. Se ja passou dessa etapa, um humano
    assumiu e o agente nao deve entrar. Conversa ja em andamento (agente ja
    respondeu antes) passa direto -- ele continua ate o fim, movendo o card
    pelo funil por conta propria."""
    etapa_inicial = settings.rd_stage_primeira_conexao_rd_id.strip()
    if not etapa_inicial:
        return True
    if _agente_ja_engajou(db, phone_number):
        return True
    return bool(deal and deal.stage_rd_id == etapa_inicial)


def _responder_com_agente(db: Session, *, phone_number: str, texto: str, wamid_recebido: str, contact_name: str | None) -> None:
    """Chama o agente e manda a resposta de volta pro lead pelo WhatsApp de
    verdade (`send_text` -- valido aqui porque o LEAD acabou de mandar
    mensagem, o que abre a janela de 24h de texto livre; nao precisa de
    template pra RESPONDER, so pra iniciar contato -- ver
    webhooks/processor.py::_iniciar_atendimento_agente)."""
    try:
        if not _pode_responder_automaticamente(phone_number):
            logger.info(
                "Agente: numero %s fora da lista de numeros liberados pra teste -- mensagem guardada, sem resposta automatica.",
                phone_number,
            )
            return

        de_teste = _e_numero_de_teste(phone_number)
        deal = _deal_por_telefone(db, phone_number)

        if not de_teste and not _lead_de_trafego_pago(db, deal):
            logger.info(
                "Agente: numero %s nao tem negociacao de trafego pago (deal=%s) -- mensagem guardada, sem resposta. "
                "Se o card ainda nao sincronizou, o reprocessamento tenta de novo.",
                phone_number, deal.rd_id if deal else None,
            )
            return

        if not de_teste and not _pode_iniciar_atendimento(db, phone_number, deal):
            logger.info(
                "Agente: numero %s -- negociacao %s ja passou de 'Primeira Conexao' (etapa %s) e o agente ainda nao "
                "havia engajado -- humano assumiu, mensagem guardada sem resposta.",
                phone_number, deal.rd_id if deal else None, deal.stage_rd_id if deal else None,
            )
            return

        historico = _carregar_historico(db, phone_number, exceto_wamid=wamid_recebido)
        deal_rd_id = deal.rd_id if deal else None
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


def _e_comando_reset(texto: str | None) -> bool:
    palavra = settings.whatsapp_agent_reset_keyword.strip().lower()
    return bool(palavra) and (texto or "").strip().lower() == palavra


def _resetar_conversa(db: Session, phone_number: str) -> None:
    """Apaga o historico de whatsapp_messages desse telefone -- o agente
    reconstroi o contexto so a partir dessa tabela (ver `_carregar_historico`),
    entao apagar aqui = comecar do zero. Nao toca no CRM nem no llm_call_log
    (auditoria de custo continua). Manda uma confirmacao curta."""
    apagadas = (
        db.query(WhatsappMessage).filter(WhatsappMessage.phone_number == phone_number).delete(synchronize_session=False)
    )
    db.commit()
    logger.info("Agente: RESET pedido por %s -- %s mensagens apagadas.", phone_number, apagadas)
    try:
        WhatsappClient().send_text(to=phone_number, body="Conversa reiniciada. Pode mandar a primeira mensagem de novo.")
    except Exception:  # noqa: BLE001
        logger.exception("Agente: falha ao confirmar reset pro numero %s.", phone_number)


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
                if _e_comando_reset(texto):
                    _resetar_conversa(db, numero)
                    continue
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
