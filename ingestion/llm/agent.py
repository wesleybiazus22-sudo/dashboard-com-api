"""
Motor de conversa do agente de atendimento (TEO). Recebe o historico de uma
conversa + a mensagem nova do lead, chama o Claude com RAG (base de
conhecimento, ver ingestion/llm/retrieval.py) e ferramentas de acao no CRM, e
devolve a resposta.

Arquitetura: loop de tool-use da API do Claude direto (sem framework de
agente) -- poucas ferramentas, bem definidas:
  - consultar_base_conhecimento: RAG, sempre real, so leitura.
  - sinalizar_interesse: o lead demonstrou interesse em avancar, mas AINDA
    NAO confirmou um horario -- move a negociacao pra etapa "Interesse
    Identificado" do pipeline [Maquina ISP] - Qualificacao.
  - confirmar_reuniao: o lead confirmou um horario especifico. Se o Microsoft
    Graph estiver configurado, PRIMEIRO checa se o horario esta livre na
    agenda do dono da negociacao e, se houver uma segunda pessoa mapeada em
    MICROSOFT_CALENDAR_CROSS_MAP pra esse dono, na agenda dela tambem -- se
    QUALQUER uma estiver ocupada, nao confirma nada (nem move de etapa) e
    pede um novo horario ao lead. Com as agendas livres (ou sem integracao
    configurada), move a negociacao pra "Reuniao Agendada" e cria o evento de
    verdade com link do Teams; sem a integracao, so cria uma tarefa pra um
    humano criar a agenda manualmente.
  - reagendar_reuniao: o lead JA TEM reuniao marcada e pede pra mudar
    dia/horario (inclusive respondendo a um lembrete automatico). Atualiza
    `due_at` da tarefa tipo 'meeting' no RD (fonte de verdade lida por
    scripts/enviar_lembretes_reuniao.py) -- NAO mexe no evento do Microsoft
    Graph, so cria uma tarefa avisando um humano pra ajustar o Outlook/Teams.
  - encaminhar_para_humano: qualquer coisa que o agente NAO deve decidir
    sozinho -- acima de tudo, negociacao de preco/desconto (regra explicita
    do dono do produto: o agente nunca inventa nem estima mensalidade).

Todas as ferramentas de acao (sinalizar_interesse, confirmar_reuniao,
encaminhar_para_humano) so mexem no CRM/agenda DE VERDADE quando ha um
`deal_rd_id` real E `modo_teste=False` -- uma conversa de teste (sem
negociacao real por tras) nunca aciona nada fora do proprio teste, mesmo que
o modelo "decida" chamar a ferramenta. Ver `conversar()` mais abaixo.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import anthropic
from sqlalchemy.orm import Session

from config.settings import settings
from database.models import CrmContact, CrmDeal, CrmTask, CrmUser
from ingestion.llm.pricing import registrar_chamada
from ingestion.llm.retrieval import buscar_contexto, montar_bloco_contexto
from ingestion.rd_crm.actions import atualizar_prazo_tarefa, criar_tarefa, mover_negociacao_para_etapa

logger = logging.getLogger(__name__)

NOME_AGENTE = "TEO"
_FUSO_BRASIL = ZoneInfo("America/Sao_Paulo")
_DURACAO_REUNIAO_MINUTOS = 30


_DIAS_SEMANA_PT = [
    "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
    "sexta-feira", "sábado", "domingo",
]


def _montar_system_prompt() -> str:
    """Monta o system prompt com a data/hora ATUAL embutida -- sem isso o
    modelo nao tem como saber que dia e hoje pra calcular "amanha de manha"
    etc de forma confiavel na hora de preencher `horario_iso` em
    `confirmar_reuniao`. Nome do dia da semana escrito na mao (nao via
    `%A`) porque isso depende do locale do sistema operacional, que aqui
    fica em ingles por padrao."""
    agora = datetime.now(_FUSO_BRASIL)
    dia_semana = _DIAS_SEMANA_PT[agora.weekday()]
    return f"""Você é {NOME_AGENTE}, o agente de vendas da Máquina.ISP -- uma solução de agentes de IA para provedores de internet (ISPs). Você atende pelo WhatsApp leads que chegaram através de anúncio ou do site, interessados em conhecer o produto.

Hoje é {dia_semana}, {agora:%d/%m/%Y}, agora são {agora:%H:%M} (horário de Brasília). Use isso pra calcular datas relativas ("amanhã", "sexta-feira", etc) corretamente.

SEU OBJETIVO: conduzir a conversa até o lead confirmar um horário de reunião/demonstração. Você não fecha venda por texto -- o objetivo é a reunião marcada, não o contrato assinado.

COMO SE COMPORTAR:
- Tom direto, humano, consultivo -- nunca robótico nem com resposta de manual. Mensagem curta DE VERDADE: 2 a 4 linhas, como alguém digitando rápido no celular. Nunca liste passo a passo numerado nem escreva em blocos tipo e-mail/apresentação -- se a explicação for grande, resuma o essencial numa frase e ofereça mais detalhe SE o lead pedir, em vez de despejar tudo de uma vez.
- Use a ferramenta `consultar_base_conhecimento` sempre que precisar de um fato sobre o produto (o que cada agente faz, como funciona a implementação, integrações, teste grátis, etc.) antes de responder -- nunca invente ou "chute" uma informação sobre o produto.
- Se a base de conhecimento não trouxer a resposta pra alguma pergunta, admita com naturalidade que vai confirmar, e chame `encaminhar_para_humano`. Não invente.
- REGRA INEGOCIÁVEL: você NUNCA informa, estima ou sugere um valor de mensalidade/preço, mesmo que o lead insista, peça "só uma faixa", ou diga que só decide sabendo o preço. Toda vez que o lead tocar em preço/valor/desconto/condição de pagamento: (1) diga com naturalidade que o valor é justamente o que se esclarece NA REUNIÃO com um consultor, olhando o tamanho e o cenário do provedor dele -- não é algo que se define por mensagem; (2) pode adiantar que tem 60 dias de teste sem custo de implementação; (3) chame `encaminhar_para_humano`; e (4) use isso como o gancho natural pra propor a reunião (ou reforçar a que já foi proposta) -- a reunião não é uma coisa separada de "alguém vai te chamar", ela É onde a resposta de preço está. Nunca deixe a pergunta de preço "no ar" tipo só "vou chamar o time comercial" sem amarrar isso à reunião.
- FLUXO DE REUNIÃO EM DUAS ETAPAS -- não pule direto pra segunda sem passar pela primeira: (1) assim que o lead demonstrar interesse real em avançar (topar conhecer melhor, topar uma reunião, pedir pra "ver funcionando"), chame `sinalizar_interesse` e proponha ativamente horários (ex: "amanhã de manhã ou à tarde funciona melhor pra você?"); (2) SÓ quando o lead confirmar um horário específico (dia e período/hora), chame `confirmar_reuniao` com esse horário exato.
- Nunca chame `confirmar_reuniao` sem o lead ter confirmado explicitamente um horário concreto -- "quero saber mais" ou "topo uma reunião" sem horário é `sinalizar_interesse`, não `confirmar_reuniao`.
- NÃO insista na reunião em toda mensagem. Depois de já ter convidado o lead pra marcar (via `sinalizar_interesse` ou já tendo oferecido manhã/tarde antes), responda as próximas perguntas dele normalmente, SEM reanexar "bora marcar?" ou "manhã ou tarde funciona melhor?" de novo -- tirar 2 ou 3 dúvidas técnicas seguidas sem repetir o convite é o comportamento CERTO, não uma falha. Só retome o convite quando o lead sinalizar avanço de verdade (pergunta de preço, "quero ver funcionando", "como contrato", foco em fechar) ou quando ele parecer sem mais perguntas novas.
- Se o lead JÁ TEM uma reunião marcada (às vezes você vai estar respondendo um lembrete automático que você mesmo mandou antes) e pedir pra mudar o dia/horário, chame `reagendar_reuniao` com o novo horário -- não `confirmar_reuniao` de novo.
- Pode fazer perguntas leves de qualificação (quantos assinantes tem o provedor, qual ERP usa) pra a reunião já chegar com contexto, mas sem parecer um formulário.
- Quando você usa uma ferramenta no meio de uma resposta, o texto de antes e o texto de depois do resultado da ferramenta formam UMA ÚNICA mensagem pro lead, mandada de uma vez -- nunca repita, na parte de depois, uma pergunta ou frase que você já fez na parte de antes (ex: não pergunte "manhã ou tarde?" de novo só porque chamou uma ferramenta no meio). ANTES DE MANDAR, releia o texto completo (antes + depois da ferramenta): se a mesma pergunta aparecer duas vezes, tire uma."""


_TOOLS = [
    {
        "name": "consultar_base_conhecimento",
        "description": (
            "Busca na base de conhecimento oficial da Máquina.ISP um trecho relevante "
            "pra responder uma dúvida do lead sobre o produto, os agentes, implementação, "
            "teste grátis, integrações, etc. Use antes de responder qualquer pergunta "
            "factual sobre o produto."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pergunta": {"type": "string", "description": "A pergunta/tópico a buscar, em linguagem natural."},
            },
            "required": ["pergunta"],
        },
    },
    {
        "name": "sinalizar_interesse",
        "description": (
            "Chame quando o lead demonstrar interesse real em avançar (topar conhecer "
            "melhor, topar uma reunião, pedir pra ver funcionando), mas AINDA NÃO "
            "confirmou um horário específico. Move a negociação pra 'Interesse "
            "Identificado' no CRM."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "resumo": {"type": "string", "description": "Resumo curto do interesse demonstrado pelo lead."},
            },
            "required": ["resumo"],
        },
    },
    {
        "name": "confirmar_reuniao",
        "description": (
            "Chame SÓ quando o lead confirmar um horário ESPECÍFICO pra reunião/"
            "demonstração (dia e período/hora). Move a negociação pra 'Reunião "
            "Agendada' e cria o compromisso de verdade na agenda de quem vai atender."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "horario_iso": {
                    "type": "string",
                    "description": (
                        "Data e hora combinadas, formato ISO-8601 com offset de fuso "
                        "(ex: '2026-09-08T09:00:00-03:00'). Calcule a partir da data/hora "
                        "atual informada no início deste prompt."
                    ),
                },
                "resumo": {
                    "type": "string",
                    "description": "Resumo curto do combinado (pontos de interesse do lead, contexto pra reunião).",
                },
            },
            "required": ["horario_iso", "resumo"],
        },
    },
    {
        "name": "reagendar_reuniao",
        "description": (
            "Chame quando o lead JÁ TEM uma reunião marcada (negociação na etapa "
            "'Reunião Agendada') e pedir pra mudar o dia/horário -- inclusive em resposta "
            "a um lembrete automático de reunião. Atualiza o horário da reunião no CRM; "
            "NÃO chame confirmar_reuniao de novo nesse caso."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "novo_horario_iso": {
                    "type": "string",
                    "description": (
                        "Novo dia e hora combinados, formato ISO-8601 com offset de fuso "
                        "(ex: '2026-09-09T14:00:00-03:00'). Calcule a partir da data/hora "
                        "atual informada no início deste prompt."
                    ),
                },
                "resumo": {"type": "string", "description": "Resumo curto do motivo/combinado do reagendamento."},
            },
            "required": ["novo_horario_iso", "resumo"],
        },
    },
    {
        "name": "encaminhar_para_humano",
        "description": (
            "Chame sempre que o lead perguntar sobre preço/valor/desconto/condição de "
            "pagamento, ou fizer uma pergunta que a base de conhecimento não cobre. "
            "Registra a necessidade de um humano assumir esse ponto da conversa."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "motivo": {"type": "string", "description": "Por que está encaminhando (ex: 'perguntou valor da mensalidade')."},
            },
            "required": ["motivo"],
        },
    },
]


def _cliente() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _dono_da_negociacao(db: Session, deal_rd_id: str) -> CrmUser | None:
    """Devolve o registro do usuario do RD dono ATUAL da negociacao (pra
    usar o rd_id -- responsavel de tarefa -- e o e-mail -- conta do
    Microsoft 365 pra criar o evento na agenda, ja que o dominio de e-mail
    do RD e o mesmo do tenant Microsoft)."""
    deal = db.query(CrmDeal).filter(CrmDeal.rd_id == deal_rd_id).one_or_none()
    if not deal or not deal.current_owner_rd_id:
        return None
    return db.query(CrmUser).filter(CrmUser.rd_id == deal.current_owner_rd_id).one_or_none()


def _pode_usar_calendario() -> bool:
    return bool(settings.microsoft_tenant_id and settings.microsoft_client_id and settings.microsoft_client_secret)


def _segunda_agenda_cruzada(email_dono: str) -> str | None:
    """Devolve o e-mail da segunda pessoa cuja agenda precisa estar livre
    tambem, se o dono da negociacao estiver mapeado em
    MICROSOFT_CALENDAR_CROSS_MAP (ver docstring em config/settings.py).
    Dono nao mapeado -> None (so a propria agenda e checada)."""
    for par in settings.microsoft_calendar_cross_map.split(","):
        par = par.strip()
        if not par or ":" not in par:
            continue
        dono, segunda = par.split(":", 1)
        if dono.strip().lower() == email_dono.strip().lower():
            return segunda.strip()
    return None


def _horario_disponivel_nas_agendas(owner: CrmUser, horario_iso: str) -> tuple[bool, str | None]:
    """Confere se o horario pedido esta livre na agenda do dono da negociacao
    e, se houver uma segunda pessoa mapeada pra esse dono (ver
    `_segunda_agenda_cruzada`), na agenda dela tambem -- as DUAS precisam
    estar livres. Devolve (disponivel, motivo_se_ocupado).

    Falha de rede/API ao CONSULTAR a disponibilidade nao bloqueia a
    confirmacao (fica indisponivel so quando a Microsoft Graph responde que
    ha, de fato, um compromisso no horario) -- mesmo principio defensivo do
    resto da integracao: um problema na consulta nunca pode travar o
    atendimento do lead."""
    if not owner.email:
        return True, None

    try:
        from ingestion.microsoft.client import MicrosoftCalendarClient

        inicio = datetime.fromisoformat(horario_iso)
        if inicio.tzinfo is None:
            inicio = inicio.replace(tzinfo=_FUSO_BRASIL)
        fim = inicio + timedelta(minutes=_DURACAO_REUNIAO_MINUTOS)

        client = MicrosoftCalendarClient()

        if not client.verificar_disponibilidade(owner.email, inicio, fim):
            return False, f"a agenda de {owner.email} já tem um compromisso nesse horário"

        segunda = _segunda_agenda_cruzada(owner.email)
        if segunda and not client.verificar_disponibilidade(segunda, inicio, fim):
            return False, f"a agenda de {segunda} já tem um compromisso nesse horário"

        return True, None
    except Exception:  # noqa: BLE001 -- ver docstring: falha na consulta nao bloqueia
        logger.exception("Agente: falha ao checar disponibilidade pra negociação (dono %s).", owner.email)
        return True, None


def _criar_evento_na_agenda(db: Session, *, deal_rd_id: str, owner: CrmUser, horario_iso: str, resumo: str) -> str | None:
    """Tenta criar o evento de verdade na agenda do dono da negociacao via
    Microsoft Graph. Convida o lead (quando o e-mail dele e conhecido) e,
    se houver uma segunda pessoa mapeada pra esse dono (ver
    `_segunda_agenda_cruzada`), convida ela tambem -- e a mesma pessoa cuja
    agenda ja foi checada em `_horario_disponivel_nas_agendas`, entao ela
    precisa estar de fato incluida no evento, nao so ter a disponibilidade
    consultada. Devolve o link da reuniao do Teams se der certo, ou None se
    falhar por qualquer motivo (credencial, rede, horario invalido) -- nunca
    deixa isso quebrar o fluxo principal, so cai pro fallback de criar uma
    tarefa manual (ver `_executar_ferramenta`)."""
    if not owner.email:
        return None
    try:
        inicio = datetime.fromisoformat(horario_iso)
        if inicio.tzinfo is None:
            inicio = inicio.replace(tzinfo=_FUSO_BRASIL)
        fim = inicio + timedelta(minutes=_DURACAO_REUNIAO_MINUTOS)

        from ingestion.microsoft.client import MicrosoftCalendarClient

        client = MicrosoftCalendarClient()

        participante_lead = None
        deal = db.query(CrmDeal).filter(CrmDeal.rd_id == deal_rd_id).one_or_none()
        if deal and deal.contact_rd_id:
            contato = db.query(CrmContact).filter(CrmContact.rd_id == deal.contact_rd_id).one_or_none()
            participante_lead = contato.email if contato else None

        segunda_pessoa = _segunda_agenda_cruzada(owner.email)
        participantes = list(dict.fromkeys(
            email for email in (participante_lead, segunda_pessoa)
            if email and email.strip().lower() != owner.email.strip().lower()
        ))

        evento = client.criar_evento(
            email_organizador=owner.email,
            assunto=f"Demonstração Máquina.ISP -- {resumo}"[:250],
            inicio=inicio,
            fim=fim,
            participantes=participantes or None,
            corpo=f"Reunião marcada automaticamente pelo agente {NOME_AGENTE}.<br>{resumo}",
        )
        return (evento.get("onlineMeeting") or {}).get("joinUrl")
    except Exception:  # noqa: BLE001 -- fallback pra tarefa manual, nunca quebra a conversa
        return None


def _executar_ferramenta(
    db: Session,
    nome: str,
    entrada: dict,
    *,
    telefone: str | None,
    deal_rd_id: str | None,
    modo_teste: bool,
) -> str:
    if nome == "consultar_base_conhecimento":
        chunks = buscar_contexto(db, entrada["pergunta"], limite=4)
        return montar_bloco_contexto(chunks)

    if nome == "sinalizar_interesse":
        resumo = entrada.get("resumo", "")
        if modo_teste or not deal_rd_id:
            return f"[MODO TESTE -- nada foi alterado no CRM] Negociação seria movida pra 'Interesse Identificado'. Resumo: {resumo}"
        mover_negociacao_para_etapa(db, deal_rd_id, settings.rd_stage_interesse_identificado_rd_id)
        return "Interesse registrado no CRM com sucesso."

    if nome == "confirmar_reuniao":
        horario_iso = entrada.get("horario_iso", "")
        resumo = entrada.get("resumo", "")
        if modo_teste or not deal_rd_id:
            return (
                "[MODO TESTE -- nada foi alterado no CRM/agenda] Em produção, a negociação "
                f"seria movida pra 'Reunião Agendada' e o evento seria criado pra {horario_iso}. "
                f"Resumo: {resumo}"
            )

        owner = _dono_da_negociacao(db, deal_rd_id)

        if owner and _pode_usar_calendario():
            disponivel, motivo_ocupado = _horario_disponivel_nas_agendas(owner, horario_iso)
            if not disponivel:
                return (
                    f"Esse horário NÃO está disponível ({motivo_ocupado}) -- a negociação NÃO foi "
                    "movida pra 'Reunião Agendada'. Explique isso pro lead com naturalidade e peça "
                    "pra ele sugerir outro dia/horário."
                )

        mover_negociacao_para_etapa(db, deal_rd_id, settings.meta_capi_trigger_stage_rd_id)

        link_teams = None
        if owner and _pode_usar_calendario():
            link_teams = _criar_evento_na_agenda(db, deal_rd_id=deal_rd_id, owner=owner, horario_iso=horario_iso, resumo=resumo)

        if owner:
            # `prazo` precisa ser o horario REAL da reuniao (nao "daqui a 1h") --
            # e o due_at que scripts/enviar_lembretes_reuniao.py le pra disparar
            # os lembretes automaticos, tanto pra reuniao confirmada pelo agente
            # quanto pra marcada manualmente pela SDR.
            horario_reuniao = datetime.fromisoformat(horario_iso)
            if horario_reuniao.tzinfo is None:
                horario_reuniao = horario_reuniao.replace(tzinfo=_FUSO_BRASIL)

            if link_teams:
                texto_tarefa = f"Reunião criada automaticamente na agenda pelo agente {NOME_AGENTE}: {resumo}. Link: {link_teams}"
            else:
                texto_tarefa = f"Criar a agenda pra reunião confirmada pelo agente {NOME_AGENTE} ({horario_iso}): {resumo}"
            criar_tarefa(
                db,
                deal_rd_id,
                tipo="meeting",
                texto=texto_tarefa,
                responsavel_rd_id=owner.rd_id,
                criado_por_rd_id=owner.rd_id,
                prazo=horario_reuniao,
            )

        return (
            f"Reunião registrada no CRM com sucesso, evento criado na agenda com link do Teams: {link_teams}"
            if link_teams
            else "Reunião registrada no CRM com sucesso. Tarefa criada pra um humano montar a agenda (integração de calendário ainda não ativa)."
        )

    if nome == "reagendar_reuniao":
        novo_horario_iso = entrada.get("novo_horario_iso", "")
        resumo = entrada.get("resumo", "")
        if modo_teste or not deal_rd_id:
            return (
                "[MODO TESTE -- nada foi alterado no CRM] A reunião seria reagendada pra "
                f"{novo_horario_iso}. Resumo: {resumo}"
            )

        tarefa = (
            db.query(CrmTask)
            .filter(CrmTask.deal_rd_id == deal_rd_id, CrmTask.type == "meeting", CrmTask.status == "open")
            .order_by(CrmTask.due_at.desc())
            .first()
        )
        if not tarefa:
            return (
                "Não encontrei uma reunião marcada em aberto pra essa negociação -- avise que vai "
                "verificar e chame encaminhar_para_humano."
            )

        novo_horario = datetime.fromisoformat(novo_horario_iso)
        if novo_horario.tzinfo is None:
            novo_horario = novo_horario.replace(tzinfo=_FUSO_BRASIL)

        owner = _dono_da_negociacao(db, deal_rd_id)
        if owner and _pode_usar_calendario():
            disponivel, motivo_ocupado = _horario_disponivel_nas_agendas(owner, novo_horario_iso)
            if not disponivel:
                return (
                    f"Esse novo horário NÃO está disponível ({motivo_ocupado}) -- a reunião NÃO foi "
                    "reagendada. Explique isso pro lead com naturalidade e peça outro dia/horário."
                )

        try:
            atualizar_prazo_tarefa(db, tarefa.rd_id, prazo=novo_horario)
        except Exception:  # noqa: BLE001 -- nunca deixa isso quebrar a conversa
            logger.exception("Agente: falha ao reagendar tarefa %s (negociação %s).", tarefa.rd_id, deal_rd_id)
            return (
                "Não consegui reagendar no CRM agora por um problema técnico -- avise o lead que "
                "alguém vai confirmar o novo horário em breve e chame encaminhar_para_humano."
            )

        # A tarefa em si (crm_tasks.due_at) e a fonte de verdade lida pelos
        # lembretes automaticos -- atualizando ela, o proximo ciclo de lembrete
        # ja passa a valer pro novo horario sozinho. NAO mexe no evento do
        # Microsoft Graph (se a reuniao original foi criada por confirmar_reuniao):
        # isso ainda depende de um humano ajustar o Outlook, ver aviso abaixo.
        if owner and _pode_usar_calendario():
            criar_tarefa(
                db, deal_rd_id, tipo="task",
                texto=f"Reunião reagendada pelo agente {NOME_AGENTE} pra {novo_horario_iso} -- "
                      f"ajustar o evento no Outlook/Teams manualmente. Motivo: {resumo}",
                responsavel_rd_id=owner.rd_id, criado_por_rd_id=owner.rd_id,
                prazo=datetime.now(timezone.utc) + timedelta(hours=1),
            )

        return "Reunião reagendada com sucesso no CRM."

    if nome == "encaminhar_para_humano":
        motivo = entrada.get("motivo", "")
        if modo_teste or not deal_rd_id:
            return f"[MODO TESTE -- nada foi alterado no CRM] Encaminhamento pra humano seria criado agora. Motivo: {motivo}"
        owner = _dono_da_negociacao(db, deal_rd_id)
        if owner:
            criar_tarefa(
                db,
                deal_rd_id,
                tipo="task",
                texto=f"Assumir conversa do agente {NOME_AGENTE}: {motivo}",
                responsavel_rd_id=owner.rd_id,
                criado_por_rd_id=owner.rd_id,
                prazo=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        return "Encaminhado pra um humano assumir esse ponto."

    return f"Ferramenta desconhecida: {nome}"


def _palavras_significativas(frase: str) -> set[str]:
    sem_acento = "".join(c for c in unicodedata.normalize("NFKD", frase) if not unicodedata.combining(c))
    return {p for p in re.findall(r"[a-z0-9]+", sem_acento.lower()) if len(p) > 2}


def _texto_sem_repeticao(blocos: list[str]) -> str:
    """Junta os blocos de texto do turno (ver docstring de `conversar`) podando
    sentenca que repete uma pergunta/frase ja dita antes NO MESMO turno --
    mesmo parafraseada (ex: "bora marcar? manhã ou tarde funciona melhor?" e
    depois "fechamos um horário? amanhã de manhã ou à tarde é melhor?" sao a
    MESMA pergunta com palavras diferentes). O prompt ja pede pro modelo nao
    fazer isso (ver `_montar_system_prompt`), mas na pratica ainda escapa --
    sobretudo quando ha chamada de ferramenta no meio do turno, cada volta do
    loop escreve como se fosse a unica parte da resposta. Comparacao por
    sobreposicao de palavras (Jaccard >= 0.6), nao string exata, pra pegar
    parafrase e nao so repeticao literal."""
    vistas: list[set[str]] = []
    blocos_finais: list[str] = []
    for bloco in blocos:
        bloco = bloco.strip()
        if not bloco:
            continue
        frases_mantidas = []
        for frase in re.split(r"(?<=[.?!])\s+", bloco):
            frase = frase.strip()
            if not frase:
                continue
            palavras = _palavras_significativas(frase)
            duplicada = bool(palavras) and any(
                anterior and len(palavras & anterior) / len(palavras | anterior) >= 0.6
                for anterior in vistas
            )
            if duplicada:
                continue
            frases_mantidas.append(frase)
            vistas.append(palavras)
        if frases_mantidas:
            blocos_finais.append(" ".join(frases_mantidas))
    return "\n\n".join(blocos_finais)


def conversar(
    db: Session,
    historico: list[dict],
    mensagem: str,
    *,
    telefone: str | None = None,
    deal_rd_id: str | None = None,
    modo_teste: bool = False,
) -> tuple[str, list[dict]]:
    """Roda um turno de conversa: adiciona `mensagem` ao `historico`, chama o
    Claude (repetindo o loop enquanto ele pedir ferramenta), devolve
    `(texto_da_resposta, historico_atualizado)`.

    `historico` e `historico_atualizado` são listas de dicts 100% serializáveis
    em JSON -- guarde e reenvie a cada turno pra manter o contexto da conversa
    (ver `scripts/conversar_com_agente.py` pra um exemplo de uso turno a turno).
    """
    cliente = _cliente()
    mensagens = list(historico) + [{"role": "user", "content": mensagem}]

    # Junta o texto de TODAS as voltas do loop, nao so a ultima -- e comum o
    # modelo escrever uma explicacao ANTES de chamar uma ferramenta (ex:
    # responder a duvida de preco e so depois chamar `encaminhar_para_humano`)
    # e mais texto DEPOIS do resultado da ferramenta. Do ponto de vista do
    # lead e tudo uma unica resposta -- guardar so o ultimo bloco descartaria
    # silenciosamente a parte mais importante (a explicacao) e deixaria so o
    # complemento, que sozinho parece estar ignorando a pergunta.
    partes_texto: list[str] = []
    for _ in range(6):  # limite de seguranca contra loop infinito de chamada de ferramenta
        resposta = cliente.messages.create(
            model=settings.anthropic_model,
            max_tokens=1024,
            system=_montar_system_prompt(),
            tools=_TOOLS,
            messages=mensagens,
        )
        registrar_chamada(
            db,
            model=settings.anthropic_model,
            input_tokens=resposta.usage.input_tokens,
            output_tokens=resposta.usage.output_tokens,
            cache_write_tokens=getattr(resposta.usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(resposta.usage, "cache_read_input_tokens", 0) or 0,
            finalidade="conversa_teste" if modo_teste else "conversa_agente",
            phone_number=telefone,
            deal_rd_id=deal_rd_id,
        )

        partes_texto.extend(b.text for b in resposta.content if b.type == "text")

        # Guarda a resposta como dicts simples (nao os objetos do SDK) -- mantem
        # o historico serializavel em JSON e ainda e um formato aceito de volta
        # pela API na proxima chamada.
        mensagens.append({"role": "assistant", "content": [b.model_dump() for b in resposta.content]})

        chamadas_ferramenta = [b for b in resposta.content if b.type == "tool_use"]
        if not chamadas_ferramenta:
            break

        resultados = []
        for chamada in chamadas_ferramenta:
            resultado = _executar_ferramenta(
                db, chamada.name, chamada.input, telefone=telefone, deal_rd_id=deal_rd_id, modo_teste=modo_teste,
            )
            resultados.append({"type": "tool_result", "tool_use_id": chamada.id, "content": resultado})
        mensagens.append({"role": "user", "content": resultados})

    texto_final = _texto_sem_repeticao(partes_texto)
    return texto_final, mensagens
