"""Captacao presencial em evento: o vendedor cadastra o lead no formulario da
apresentacao e o agente (TEO) chama o lead no WhatsApp, dizendo QUEM pediu.

Fluxo (`registrar_lead` -> `acionar`):
1. valida e grava o lead em `evento_leads` (idempotente por `client_id`);
2. garante o card no RD CRM (organizacao + contato + negociacao);
3. manda o template de abertura, com o nome do lead e o do vendedor.

Cada etapa e RETOMAVEL: a pagina trabalha com fila e reenvia sozinha quando falta
sinal, entao o mesmo lead pode chegar varias vezes. O estado (`deal_rd_id`,
`status`) fica no banco, e uma nova chamada continua de onde parou -- nunca cria
um segundo card nem manda uma segunda mensagem.

Quando o lead RESPONDE, `whatsapp/processor.py` cai no fluxo normal: o telefone ja
tem mensagem outbound (`_agente_ja_engajou`) e o card ja existe, entao as travas
de origem e de etapa deixam o agente seguir -- mesmo mecanismo do convite manual
(`whatsapp/manual_invite.py`). `contexto_do_evento` entrega ao agente o evento e o
nome de quem pediu.
"""

import logging
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config.settings import settings
from database.models import CrmDeal, EventoLead, WhatsappMessage
from ingestion.rd_crm.actions import criar_negociacao_com_contato, criar_tarefa
from ingestion.rd_crm.client import RDCrmClient
from ingestion.whatsapp.client import WhatsappClient, normalizar_telefone_br

logger = logging.getLogger(__name__)

_FUSO = ZoneInfo("America/Sao_Paulo")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

STATUS_ENVIADO = "enviado"
STATUS_FALHOU = "falhou"          # o Meta RECUSOU (4xx): nada foi enviado, pode tentar de novo
STATUS_INCERTO = "incerto"        # timeout/5xx: pode ter sido entregue -- NAO reenviar sozinho
STATUS_SEM_TEMPLATE = "sem_template"
STATUS_DUPLICADO = "duplicado"    # esse numero ja foi chamado neste evento por outro cadastro
STATUS_DESCARTADO = "descartado"  # lead de TESTE retirado a mao: nao conta como "ja chamado" nem como contexto


class ErroDoLead(Exception):
    """Erro de regra de negocio, com o codigo HTTP que a rota deve devolver."""

    def __init__(self, mensagem: str, status_http: int = 422):
        super().__init__(mensagem)
        self.status_http = status_http


# ----------------------------------------------------------------------------
# validacao
# ----------------------------------------------------------------------------


def vendedores() -> list[str]:
    return [v.strip() for v in settings.evento_vendedores.split(",") if v.strip()]


def _hoje() -> date:
    return datetime.now(_FUSO).date()


def janela_aberta(hoje: date | None = None) -> bool:
    hoje = hoje or _hoje()
    try:
        inicio = date.fromisoformat(settings.evento_janela_inicio)
        fim = date.fromisoformat(settings.evento_janela_fim)
    except ValueError:
        return False  # data mal configurada: fecha por seguranca
    return inicio <= hoje <= fim


def primeiro_nome(nome: str | None) -> str:
    partes = (nome or "").strip().split()
    return partes[0].capitalize() if partes else ""


def _vendedor_valido(informado: str) -> str | None:
    """Devolve o nome como esta na lista (capitalizacao canonica) ou None. O nome vai
    como variavel do template: aceitar texto livre deixaria qualquer um escrever o
    que quisesse numa mensagem enviada pelo numero da empresa."""
    alvo = (informado or "").strip().casefold()
    return next((v for v in vendedores() if v.casefold() == alvo), None)


def _limpo(valor, limite: int) -> str | None:
    texto = valor.strip() if isinstance(valor, str) else ""
    return texto[:limite] or None


# ----------------------------------------------------------------------------
# entrada
# ----------------------------------------------------------------------------


def registrar_lead(
    db: Session, *, client_id: str, vendedor: str, nome: str, celular: str, email: str | None,
    consentimento: bool, capturado_em: datetime | None, extras: dict | None = None,
) -> EventoLead:
    """Valida e grava (ou recupera) o lead. Idempotente por `client_id`: reenvio do
    mesmo lead devolve a linha que ja existe, sem validar de novo a janela nem o
    teto -- um lead ja aceito nao pode ser recusado so porque o sinal voltou
    depois do fim da janela."""
    existente = db.query(EventoLead).filter(EventoLead.client_id == client_id).one_or_none()
    if existente is not None:
        return existente

    if not consentimento:
        raise ErroDoLead("O lead precisa autorizar o contato por WhatsApp.")
    if not janela_aberta():
        raise ErroDoLead("Captacao fora do periodo do evento.", status_http=403)

    desde = datetime.now(timezone.utc) - timedelta(hours=1)
    if db.query(EventoLead).filter(EventoLead.created_at >= desde).count() >= settings.evento_max_leads_por_hora:
        raise ErroDoLead("Limite de leads por hora atingido.", status_http=429)

    vendedor_ok = _vendedor_valido(vendedor)
    if vendedor_ok is None:
        raise ErroDoLead("Responsavel pelo cadastro nao reconhecido.")
    nome_ok = _limpo(nome, 120)
    if not nome_ok:
        raise ErroDoLead("Informe o nome do lead.")
    telefone = normalizar_telefone_br(celular)
    if not telefone:
        raise ErroDoLead("Celular invalido.")

    email_ok = _limpo(email, 200)
    if email_ok and not _EMAIL.match(email_ok):
        email_ok = None  # e-mail torto nao pode impedir o contato por WhatsApp

    lead = EventoLead(
        client_id=client_id, evento=settings.evento_nome, vendedor=vendedor_ok, nome=nome_ok,
        telefone=telefone, email=email_ok, consentimento_whatsapp=True, capturado_em=capturado_em,
        status="recebido",
    )
    _aplicar_etapa2(lead, extras or {})
    db.add(lead)
    try:
        db.commit()
    except IntegrityError:
        # Duas chamadas simultaneas com o mesmo client_id (a pagina reenviou depois de um
        # timeout): a outra gravou primeiro. Devolve a linha dela em vez de dar erro.
        db.rollback()
        return db.query(EventoLead).filter(EventoLead.client_id == client_id).one()
    return lead


def _aplicar_etapa2(lead: EventoLead, dados: dict) -> bool:
    """Copia os campos da etapa 2 que vieram preenchidos. Devolve True se algo mudou."""
    mudou = False
    for campo, limite in (("provedor", 200), ("cidade", 120), ("uf", 2), ("erp", 120), ("observacoes", 2000)):
        valor = _limpo(dados.get(campo), limite)
        if valor is not None and valor != getattr(lead, campo):
            setattr(lead, campo, valor)
            mudou = True
    assinantes = dados.get("assinantes")
    try:
        assinantes = int(assinantes) if assinantes not in (None, "") else None
    except (TypeError, ValueError):
        assinantes = None
    if assinantes and assinantes > 0 and assinantes != lead.assinantes:
        lead.assinantes = assinantes
        mudou = True
    return mudou


# ----------------------------------------------------------------------------
# acionamento
# ----------------------------------------------------------------------------


def acionar(db: Session, lead: EventoLead) -> EventoLead:
    """Garante o card e manda a abertura. Ja enviado (ou duplicado): nao faz nada."""
    if lead.status in (STATUS_ENVIADO, STATUS_DUPLICADO, STATUS_DESCARTADO):
        return lead
    # `incerto` NAO e reenviado aqui: um timeout pode ter entregue a mensagem. So o
    # vendedor decide reenviar, de forma explicita (`reenviar`).
    if lead.status == STATUS_INCERTO:
        return lead

    lead_id = lead.id
    if not _reivindicar(db, lead_id):
        return lead  # outra chamada deste mesmo lead esta em andamento: a pagina tenta de novo
    try:
        db.refresh(lead)  # a outra chamada pode ter terminado entre a leitura e a reivindicacao
        if lead.status in (STATUS_ENVIADO, STATUS_DUPLICADO, STATUS_INCERTO, STATUS_DESCARTADO):
            return lead
        _garantir_card(db, lead)
        _enviar_abertura(db, lead)
    finally:
        _liberar(db, lead_id)
    return lead


def _reivindicar(db: Session, lead_id: str, segundos: int = 120) -> bool:
    """Marca o lead como "em processamento" de forma atomica (um UPDATE condicional).
    Sem isto, se a pagina estoura o tempo (o servidor gratuito leva ~50s pra acordar) e
    reenvia enquanto a 1a chamada ainda roda, as duas veem o lead sem card e AMBAS
    criam card e mandam o template. O prazo evita que uma chamada morta no meio
    trave o lead para sempre."""
    agora = datetime.now(timezone.utc)
    alterados = (
        db.query(EventoLead)
        .filter(EventoLead.id == lead_id, or_(EventoLead.processando_ate.is_(None), EventoLead.processando_ate < agora))
        .update({"processando_ate": agora + timedelta(seconds=segundos)}, synchronize_session=False)
    )
    db.commit()
    return alterados == 1


def _liberar(db: Session, lead_id: str) -> None:
    try:
        db.rollback()  # se algo estourou no meio, a sessao precisa estar limpa antes do UPDATE
        db.query(EventoLead).filter(EventoLead.id == lead_id).update({"processando_ate": None}, synchronize_session=False)
        db.commit()
    except Exception:  # noqa: BLE001 -- o prazo da reivindicacao expira sozinho
        db.rollback()
        logger.exception("Evento: falha ao liberar o lead %s.", lead_id)


def reenviar(db: Session, lead: EventoLead) -> EventoLead:
    """Reenvio EXPLICITO (o vendedor confirmou que a mensagem nao chegou)."""
    if lead.status == STATUS_INCERTO:
        lead.status = "recebido"
        db.commit()
    return acionar(db, lead)


def _garantir_card(db: Session, lead: EventoLead) -> None:
    if lead.deal_rd_id:
        return

    # Import adiado: whatsapp.processor importa este modulo (contexto do agente).
    from whatsapp.processor import _deal_por_telefone

    existente = _deal_por_telefone(db, lead.telefone)
    if existente is not None:
        # O lead ja esta no CRM (veio de trafego pago, Melhor Venda...). Nao duplica o
        # card -- so vincula, pra conversa do agente cair nele.
        lead.deal_rd_id = existente.rd_id
        db.commit()
        return

    owner = settings.whatsapp_agent_owner_padrao_rd_id
    negociacao = criar_negociacao_com_contato(
        db,
        # Ainda nao sabemos o provedor (etapa 2). O nome temporario identifica o card
        # ate la; `atualizar_etapa2` troca pelo nome do provedor.
        nome_empresa=lead.provedor or f"{lead.nome} -- {lead.evento}",
        nome_contato=lead.nome,
        telefone=lead.telefone,
        owner_rd_id=owner,
        pipeline_rd_id=settings.rd_pipeline_maquina_isp_qualificacao_rd_id,
        stage_rd_id=settings.rd_stage_primeira_conexao_rd_id,
        source_rd_id=settings.rd_source_evento_rd_id,
        email=lead.email,
        quantidade_assinantes=str(lead.assinantes) if lead.assinantes else None,
    )
    lead.deal_rd_id = negociacao["id"]
    db.commit()

    # O card do RD nao tem campo pra "quem captou" -- a tarefa deixa isso visivel pra
    # quem for trabalhar o lead. Falha aqui nao pode desfazer o card nem barrar o envio.
    try:
        criar_tarefa(
            db, lead.deal_rd_id, tipo="whatsapp",
            texto=(
                f"Lead captado por {lead.vendedor} no {lead.evento}. O agente TEO foi acionado "
                "por WhatsApp -- acompanhar a conversa e dar sequencia."
            ),
            responsavel_rd_id=owner, criado_por_rd_id=owner,
            prazo=datetime.now(_FUSO) + timedelta(days=1),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Evento: falha ao criar a tarefa de acompanhamento (negociacao %s).", lead.deal_rd_id)


def _enviar_abertura(db: Session, lead: EventoLead) -> None:
    template = settings.whatsapp_agent_evento_template_name.strip()
    if not template:
        lead.status = STATUS_SEM_TEMPLATE
        lead.erro = None
        db.commit()
        return

    anterior = (
        db.query(EventoLead)
        .filter(
            EventoLead.telefone == lead.telefone, EventoLead.evento == lead.evento,
            EventoLead.status == STATUS_ENVIADO, EventoLead.id != lead.id,
        )
        .first()
    )
    if anterior is not None:
        lead.status = STATUS_DUPLICADO
        lead.erro = f"Esse numero ja recebeu o contato (cadastro de {anterior.vendedor})."
        db.commit()
        return

    try:
        resposta = WhatsappClient().send_template(
            to=lead.telefone,
            template_name=template,
            language_code=settings.whatsapp_agent_evento_template_language,
            components=[{
                "type": "body",
                "parameters": [
                    {"type": "text", "text": primeiro_nome(lead.nome) or "tudo bem"},
                    {"type": "text", "text": lead.vendedor},
                ],
            }],
            sem_retry=True,
        )
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        lead = db.get(EventoLead, lead.id)
        # 4xx = o Meta recusou, nada saiu. Qualquer outra coisa (timeout, rede, 5xx) e
        # AMBIGUA: pode ter sido entregue -- nao reenviar sozinho.
        resposta_http = getattr(exc, "response", None)
        recusou = resposta_http is not None and 400 <= resposta_http.status_code < 500
        lead.status = STATUS_FALHOU if recusou else STATUS_INCERTO
        lead.erro = str(exc)[:500]
        db.commit()
        logger.warning("Evento: envio para %s terminou como %s: %s", lead.telefone, lead.status, exc)
        return

    wamid = (resposta.get("messages") or [{}])[0].get("id")
    agora = datetime.now(timezone.utc)
    db.add(WhatsappMessage(
        wamid=wamid or f"outbound-sem-id:{lead.client_id}",
        phone_number=lead.telefone,
        direction="outbound",
        message_type="template",
        text_body=f"[template: {template} -- {lead.evento}, captado por {lead.vendedor}]",
        contact_name=lead.nome,
        deal_rd_id=lead.deal_rd_id,
        raw=resposta,
        occurred_at=agora,
    ))
    lead.status = STATUS_ENVIADO
    lead.erro = None
    lead.template_wamid = wamid
    lead.enviado_em = agora
    db.commit()
    logger.info("Evento: abertura enviada para %s (%s), captado por %s.", lead.nome, lead.telefone, lead.vendedor)


# ----------------------------------------------------------------------------
# etapa 2
# ----------------------------------------------------------------------------


def atualizar_etapa2(db: Session, lead: EventoLead, dados: dict) -> EventoLead:
    """Grava os dados da etapa 2 e, se o card ja existe, leva o que o RD tem
    campo pra receber: nome do provedor (negociacao e organizacao), assinantes
    (`quantidade-de-clientes`, o mesmo campo das demais negociacoes), cidade e UF
    (campos `cidade`/`estado` da organizacao). ERP e observacoes nao tem campo
    no RD: ficam em `evento_leads` e, se houver, viram uma tarefa."""
    if not _aplicar_etapa2(lead, dados):
        return lead
    db.commit()

    if not lead.deal_rd_id:
        return lead  # card ainda nao criado: nasce ja com esses dados

    deal = db.query(CrmDeal).filter(CrmDeal.rd_id == lead.deal_rd_id).one_or_none()
    org_id = deal.organization_rd_id if deal else None
    client = RDCrmClient(db)
    try:
        corpo_deal: dict = {}
        if lead.provedor:
            corpo_deal["name"] = lead.provedor
        if lead.assinantes:
            corpo_deal["custom_fields"] = {"quantidade-de-clientes": str(lead.assinantes)}
        if corpo_deal:
            client.put(f"/deals/{lead.deal_rd_id}", json={"data": corpo_deal})
            if deal is not None and "name" in corpo_deal:
                deal.name = corpo_deal["name"]
                db.commit()

        if org_id:
            corpo_org: dict = {}
            if lead.provedor:
                corpo_org["name"] = lead.provedor
            campos = {k: v for k, v in (("cidade", lead.cidade), ("estado", lead.uf)) if v}
            if campos:
                corpo_org["custom_fields"] = campos
            if corpo_org:
                client.put(f"/organizations/{org_id}", json={"data": corpo_org})

        if lead.observacoes or lead.erp:
            owner = settings.whatsapp_agent_owner_padrao_rd_id
            partes = [f"Anotacoes de {lead.vendedor} no {lead.evento}."]
            if lead.erp:
                partes.append(f"ERP: {lead.erp}.")
            if lead.observacoes:
                partes.append(f"Observacoes: {lead.observacoes}")
            criar_tarefa(
                db, lead.deal_rd_id, tipo="task", texto=" ".join(partes),
                responsavel_rd_id=owner, criado_por_rd_id=owner,
                prazo=datetime.now(_FUSO) + timedelta(days=1),
            )
    except Exception as exc:  # noqa: BLE001 -- o dado ja esta salvo em evento_leads; o CRM pode ser acertado depois
        db.rollback()
        logger.exception("Evento: falha ao levar a etapa 2 ao CRM (negociacao %s).", lead.deal_rd_id)
        lead = db.get(EventoLead, lead.id)
        lead.erro = f"CRM (etapa 2): {str(exc)[:300]}"
        db.commit()
    return lead


# ----------------------------------------------------------------------------
# contexto pro agente
# ----------------------------------------------------------------------------


def contexto_do_evento(db: Session, telefone: str) -> dict | None:
    """Evento + vendedor do lead mais recente com esse telefone, ou None. O agente
    usa pra dizer a quem responde QUEM pediu o contato (ver
    `_montar_contexto_dinamico` em ingestion/llm/agent.py). So conta lead cuja
    abertura saiu (ou pode ter saido) -- cadastro que nunca recebeu mensagem nao e
    "alguem que pediu pra te chamar"."""
    # Esta consulta roda em TODA resposta do agente (inclusive de lead que nada tem a ver
    # com evento). Se a tabela ainda nao existir (codigo no ar antes da migracao) ou o
    # banco falhar, o agente tem que responder normalmente, so sem esse contexto.
    # SAVEPOINT e nao `rollback()`: a mensagem recebida desta mesma requisicao ainda nao
    # foi commitada (so flushada em `_upsert_mensagem`) e um rollback total a perderia.
    try:
        with db.begin_nested():
            lead = (
                db.query(EventoLead)
                .filter(EventoLead.telefone == telefone, EventoLead.status.in_((STATUS_ENVIADO, STATUS_INCERTO)))
                .order_by(EventoLead.created_at.desc())
                .first()
            )
    except Exception:  # noqa: BLE001
        logger.exception("Evento: nao consegui ler o contexto do evento (o agente segue sem ele).")
        return None
    if lead is None:
        return None
    # `observacoes` fica DE FORA de proposito: sao anotacoes internas do vendedor
    # pra consultora ("achou caro", "decisor e o socio") e o agente poderia
    # repeti-las ao lead. Provedor, porte e ERP o proprio lead contou ao vendedor.
    return {
        "evento": lead.evento, "vendedor": lead.vendedor, "provedor": lead.provedor,
        "assinantes": lead.assinantes, "erp": lead.erp,
        "local": " / ".join(x for x in (lead.cidade, lead.uf) if x) or None,
    }
