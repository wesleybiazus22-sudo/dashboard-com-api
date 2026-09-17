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
    scripts/enviar_lembretes_reuniao.py) e, se a reuniao original foi criada
    por este agente (tem evento registrado em AgendaEventoMicrosoft), move o
    evento DE VERDADE no Outlook/Teams. Reuniao marcada manualmente pela SDR
    (sem evento registrado) cai no fallback de avisar um humano pra ajustar.
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
from database.models import AgendaEventoMicrosoft, CrmContact, CrmDeal, CrmTask, CrmUser
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


def _primeiro_nome_lead(nome_completo: str | None) -> str | None:
    if nome_completo and nome_completo.strip():
        return nome_completo.strip().split()[0].capitalize()
    return None


def _montar_system_prompt(nome_lead: str | None = None) -> str:
    """Monta o system prompt com a data/hora ATUAL embutida -- sem isso o
    modelo nao tem como saber que dia e hoje pra calcular "amanha de manha"
    etc de forma confiavel na hora de preencher `horario_iso` em
    `confirmar_reuniao`. Nome do dia da semana escrito na mao (nao via
    `%A`) porque isso depende do locale do sistema operacional, que aqui
    fica em ingles por padrao.

    `nome_lead` (primeiro nome, ja extraido) personaliza a conversa -- sem
    isso o modelo nao tem como saber com quem esta falando fora do template
    de abertura (que usa o nome direto, sem passar pelo LLM)."""
    agora = datetime.now(_FUSO_BRASIL)
    dia_semana = _DIAS_SEMANA_PT[agora.weekday()]
    linha_nome = (
        f'\nVocê está falando com {nome_lead}. Use o nome dele de vez em quando, com naturalidade -- não em toda mensagem, isso soa forçado.'
        if nome_lead else ""
    )
    return f"""Você é {NOME_AGENTE}, o agente de vendas da Máquina.ISP -- uma solução de agentes de IA para provedores de internet (ISPs). Você atende pelo WhatsApp leads que chegaram através de anúncio ou do site, interessados em conhecer o produto.

Hoje é {dia_semana}, {agora:%d/%m/%Y}, agora são {agora:%H:%M} (horário de Brasília). Use isso pra calcular datas relativas ("amanhã", "sexta-feira", etc) corretamente.{linha_nome}

SEU OBJETIVO: conduzir a conversa até o lead confirmar um horário de reunião/demonstração. Você não fecha venda por texto -- o objetivo é a reunião marcada, não o contrato assinado.

COMO SE COMPORTAR:
- Tom direto, humano, consultivo -- nunca robótico nem com resposta de manual. Nunca liste passo a passo numerado nem escreva em blocos tipo e-mail/apresentação -- se a explicação for grande, resuma o essencial e ofereça mais detalhe SE o lead pedir, em vez de despejar tudo de uma vez.
- REGRA DE FORMATO OBRIGATÓRIA (não é sugestão de estilo, é formatação que você deve seguir sempre): cada parágrafo tem no máximo 1-2 frases curtas. Sempre que a resposta tiver mais de uma ideia (ex: uma explicação + uma pergunta, ou responder um ponto e puxar outro assunto -- o caso mais comum), quebre em parágrafos separados por uma linha em branco entre eles (o texto deve ter \n\n entre os parágrafos). NUNCA amontoe duas ideias no mesmo parágrafo, mesmo que o texto total seja curto -- isso vale mais do que o número total de linhas da mensagem. Pense em 2-3 balões curtos e separados no WhatsApp, não um texto corrido de uma vez. Exemplo de formatação CERTA (repare a linha em branco entre cada ideia):
"Basicamente: são agentes de IA que cuidam de venda, cobrança, atendimento e retenção do seu provedor, direto no ERP/CRM que você já usa.

Funciona 24h, sem precisar trocar de sistema.

Me conta: quantos assinantes vocês têm hoje, e qual ERP usam?"
Exemplo de formatação ERRADA (as mesmas 3 ideias, mas grudadas -- NUNCA faça isso): "Basicamente: são agentes de IA que cuidam de venda, cobrança, atendimento e retenção do seu provedor, direto no ERP/CRM que você já usa. Funciona 24h, sem precisar trocar de sistema. Me conta: quantos assinantes vocês têm hoje, e qual ERP usam?"
- ENTENDA O CENÁRIO ANTES DE EMPURRAR PRODUTO OU REUNIÃO: assim que o lead disser que quer saber mais (a primeira resposta de verdade da conversa), pergunte -- numa pergunta só, natural, sem parecer formulário -- quantos assinantes o provedor tem e qual ERP/sistema usa, ANTES de espichar o pitch do produto ou propor horário. Isso faz cada resposta seguinte (inclusive o convite pra reunião, quando chegar a hora) soar sob medida pro provedor dele, não um discurso pronto que serviria pra qualquer um.
- Use a ferramenta `consultar_base_conhecimento` sempre que precisar de um fato sobre o produto (o que cada agente faz, como funciona a implementação, integrações, teste grátis, etc.) antes de responder -- nunca invente ou "chute" uma informação sobre o produto.
- Se a base de conhecimento não trouxer a resposta pra alguma pergunta, admita com naturalidade que vai confirmar, e chame `encaminhar_para_humano`. Não invente.
- REGRA INEGOCIÁVEL: você NUNCA informa, estima ou sugere um valor de mensalidade/preço, mesmo que o lead insista, peça "só uma faixa", ou diga que só decide sabendo o preço. Toda vez que o lead tocar em preço/valor/desconto/condição de pagamento: (1) diga com naturalidade que o valor é justamente o que se esclarece NA REUNIÃO com um consultor, olhando o tamanho e o cenário do provedor dele -- não é algo que se define por mensagem; (2) pode adiantar que tem 60 dias de teste sem custo de implementação; (3) chame `encaminhar_para_humano`; e (4) use isso como o gancho natural pra propor a reunião (ou reforçar a que já foi proposta) -- a reunião não é uma coisa separada de "alguém vai te chamar", ela É onde a resposta de preço está. Nunca deixe a pergunta de preço "no ar" tipo só "vou chamar o time comercial" sem amarrar isso à reunião.
- FLUXO DE REUNIÃO EM DUAS ETAPAS -- não pule direto pra segunda sem passar pela primeira, e não pule pra primeira sem antes entender o cenário (ver regra acima): (1) assim que o lead demonstrar interesse real em avançar (topar conhecer melhor, topar uma reunião, pedir pra "ver funcionando"), chame `sinalizar_interesse` e proponha ativamente horários (ex: "amanhã de manhã ou à tarde funciona melhor pra você?"); (2) SÓ quando o lead confirmar um horário específico (dia e período/hora), chame `confirmar_reuniao` com esse horário exato.
- Nunca chame `confirmar_reuniao` sem o lead ter confirmado explicitamente um horário concreto -- "quero saber mais" ou "topo uma reunião" sem horário é `sinalizar_interesse`, não `confirmar_reuniao`.
- NÃO insista na reunião em toda mensagem. Depois de já ter convidado o lead pra marcar (via `sinalizar_interesse` ou já tendo oferecido manhã/tarde antes), responda as próximas perguntas dele normalmente, SEM reanexar "bora marcar?" ou "manhã ou tarde funciona melhor?" de novo. Pedir mais detalhe técnico ou um exemplo (ex: "como funciona?", "me dá um exemplo", "manda com botão?") é o lead ainda ENTENDENDO o produto, NÃO é sinal de avanço -- responda a dúvida e siga em frente sem repetir o convite. Tirar 2 ou 3 dúvidas técnicas seguidas sem repetir o convite é o comportamento CERTO, não uma falha. Só retome o convite quando o lead sinalizar avanço de verdade (pergunta de preço, "quero ver funcionando", "como contrato", foco em fechar) ou quando ele parecer sem mais perguntas novas.
- Se o lead JÁ TEM uma reunião marcada (às vezes você vai estar respondendo um lembrete automático que você mesmo mandou antes) e pedir pra mudar o dia/horário, chame `reagendar_reuniao` com o novo horário -- não `confirmar_reuniao` de novo.
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

        # Titulo curto de proposito ("Empresa + Maquina ISP") -- o resumo
        # detalhado (o que o lead contou, o que ele quer ver) fica so no corpo
        # do evento e na tarefa do CRM (ver texto_tarefa em confirmar_reuniao),
        # nao no titulo, que ficava enorme e dificil de ler na agenda.
        nome_cliente = (deal.name if deal and deal.name else resumo).strip()
        evento = client.criar_evento(
            email_organizador=owner.email,
            assunto=f"{nome_cliente} + Máquina ISP"[:250],
            inicio=inicio,
            fim=fim,
            participantes=participantes or None,
            corpo=f"Reunião marcada automaticamente pelo agente {NOME_AGENTE}.<br>{resumo}",
        )

        # Guarda o ID do evento pra `reagendar_reuniao` conseguir mover ele de
        # verdade depois -- sem isso nao ha como reencontrar o evento no Graph
        # a partir so do deal_rd_id.
        evento_id = evento.get("id")
        if evento_id:
            registro = db.query(AgendaEventoMicrosoft).filter(AgendaEventoMicrosoft.deal_rd_id == deal_rd_id).one_or_none()
            if registro is None:
                registro = AgendaEventoMicrosoft(deal_rd_id=deal_rd_id)
                db.add(registro)
            registro.email_organizador = owner.email
            registro.evento_id = evento_id
            registro.web_link = evento.get("webLink")
            registro.atualizado_em = datetime.now(timezone.utc)
            db.commit()

        return (evento.get("onlineMeeting") or {}).get("joinUrl")
    except Exception:  # noqa: BLE001 -- fallback pra tarefa manual, nunca quebra a conversa
        return None


def _mover_evento_na_agenda(db: Session, *, deal_rd_id: str, novo_horario_iso: str) -> bool:
    """Tenta mover DE VERDADE o evento do Microsoft Graph que
    `_criar_evento_na_agenda` criou (achado via `AgendaEventoMicrosoft`, pelo
    ID guardado na criacao). Devolve True se moveu, False se nao ha evento
    registrado pra essa negociacao (ex: reuniao marcada manualmente pela SDR,
    sem integracao) ou se a chamada falhou -- nesses casos quem chama cai no
    fallback de avisar um humano pra ajustar manualmente."""
    registro = db.query(AgendaEventoMicrosoft).filter(AgendaEventoMicrosoft.deal_rd_id == deal_rd_id).one_or_none()
    if not registro:
        return False
    try:
        inicio = datetime.fromisoformat(novo_horario_iso)
        if inicio.tzinfo is None:
            inicio = inicio.replace(tzinfo=_FUSO_BRASIL)
        fim = inicio + timedelta(minutes=_DURACAO_REUNIAO_MINUTOS)

        from ingestion.microsoft.client import MicrosoftCalendarClient

        MicrosoftCalendarClient().atualizar_evento(
            email_organizador=registro.email_organizador, evento_id=registro.evento_id, inicio=inicio, fim=fim,
        )
        registro.atualizado_em = datetime.now(timezone.utc)
        db.commit()
        return True
    except Exception:  # noqa: BLE001 -- fallback pra tarefa manual, nunca quebra a conversa
        logger.exception("Agente: falha ao mover evento do Graph pra negociação %s.", deal_rd_id)
        return False


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
        if not settings.whatsapp_agent_confirmacoes_reuniao_agendada_ativas:
            # STANDBY (ver config/settings.py) -- negociacao ja em "Reuniao Agendada"
            # some do RD minutos depois de escrita nossa, causa ainda sob investigacao
            # com o suporte do RD. Nao escreve nada, so avisa e escala pra humano.
            return (
                "[EM STANDBY -- reagendamento automatico desligado temporariamente, nada foi "
                "alterado no CRM/agenda] Avise o lead com naturalidade que alguém do time vai "
                "confirmar o reagendamento em breve, e chame encaminhar_para_humano."
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
        # ja passa a valer pro novo horario sozinho. Se a reuniao original foi
        # criada por `confirmar_reuniao` (tem evento registrado em
        # AgendaEventoMicrosoft), move o evento DE VERDADE no Outlook/Teams;
        # senao (ex: reuniao marcada manualmente pela SDR), cai no fallback de
        # avisar um humano pra ajustar.
        evento_movido = _mover_evento_na_agenda(db, deal_rd_id=deal_rd_id, novo_horario_iso=novo_horario_iso)

        if owner and _pode_usar_calendario() and not evento_movido:
            criar_tarefa(
                db, deal_rd_id, tipo="task",
                texto=f"Reunião reagendada pelo agente {NOME_AGENTE} pra {novo_horario_iso} -- "
                      f"ajustar o evento no Outlook/Teams manualmente. Motivo: {resumo}",
                responsavel_rd_id=owner.rd_id, criado_por_rd_id=owner.rd_id,
                prazo=datetime.now(timezone.utc) + timedelta(hours=1),
            )

        return (
            "Reunião reagendada com sucesso -- CRM e o evento no Outlook/Teams já atualizados."
            if evento_movido
            else "Reunião reagendada com sucesso no CRM."
        )

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


# Gatilho de frase de CONFIRMACAO (ver segundo sinal de `_texto_sem_repeticao`
# logo abaixo) -- palavras que tipicamente aparecem quando o modelo esta
# declarando "isso ja esta certo/feito", nao perguntando nem explicando algo
# novo.
_PALAVRAS_CONFIRMACAO = {
    "combinado", "confirmado", "confirmada", "certo", "pronto", "marcado",
    "marcada", "fechado", "fechada", "mantida", "mantido", "reagendado",
    "reagendada", "agendado", "agendada", "tudo",
}
_DIAS_SEMANA_SEM_ACENTO = {"segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"}


def _horario_mencionado(palavras: set[str]) -> set[str]:
    """Extrai, de um conjunto de palavras ja normalizadas (ver
    `_palavras_significativas`), so os tokens que parecem se referir a um
    horario/dia especifico (ex: "15h", "hoje", "amanha", "sexta") -- usado
    pra achar duplicacao que o Jaccard de `_texto_sem_repeticao` sozinho NAO
    pega: duas frases com palavras bem diferentes que ainda assim confirmam
    o MESMO horario duas vezes (ex: "combinado, mantida pra hoje às 15h" e,
    logo depois, "tudo certo, marcado pra hoje às 15h" -- so 3 das 9 palavras
    unicas se repetem, Jaccard 0.33, mas e a mesma confirmacao duas vezes)."""
    return {p for p in palavras if re.fullmatch(r"\d{1,2}h\d{0,2}", p) or p in ({"hoje", "amanha"} | _DIAS_SEMANA_SEM_ACENTO)}


def _texto_sem_repeticao(blocos: list[str]) -> str:
    """Junta os blocos de texto do turno (ver docstring de `conversar`) podando
    sentenca que repete uma pergunta/frase/confirmacao ja feita antes NO MESMO
    turno -- mesmo parafraseada. O prompt ja pede pro modelo nao fazer isso
    (ver `_montar_system_prompt`), mas na pratica ainda escapa -- sobretudo
    quando ha chamada de ferramenta no meio do turno (o modelo confirma ANTES
    de chamar a ferramenta, "vai dar certo", e confirma DE NOVO depois de ver
    o resultado -- duas frases com vocabulario bem diferente, mesmo horario).

    Dois sinais de duplicata, cada um pega um padrao que o outro perde:
    1. CONTENCAO de palavras (|intersecao| / |menor conjunto| >= 0.7) -- pega
       repeticao/parafrase proxima, mesmo quando uma das frases tem "recheio"
       extra em volta do miolo repetido (ex: "Amanhã de manhã ou à tarde
       funciona melhor?" vs "Enquanto isso, me diz: amanhã de manhã ou à
       tarde fica melhor pra essa reunião?" -- a segunda tem varias palavras
       a mais, o que derrubaria um Jaccard tradicional pra bem abaixo de
       qualquer limite razoavel mesmo sendo a MESMA pergunta; contencao mede
       o quanto do conjunto MENOR esta contido no outro, robusto a isso).
    2. Mesmo horario/dia mencionado (`_horario_mencionado`) numa frase que
       tambem tem cara de confirmacao (`_PALAVRAS_CONFIRMACAO`) -- pega
       confirmacao duplicada com vocabulario bem diferente (caso do
       reagendamento acima: "combinado, mantida pra hoje às 15h" vs "tudo
       certo, marcado pra hoje às 15h" -- so 3 de 9 palavras unicas em comum,
       nenhuma metrica de sobreposicao pura pegaria isso)."""
    vistas: list[set[str]] = []
    horarios_confirmados: list[set[str]] = []
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
            duplicada_por_contencao = bool(palavras) and any(
                anterior and len(palavras & anterior) / min(len(palavras), len(anterior)) >= 0.7
                for anterior in vistas
            )
            horario = _horario_mencionado(palavras) if palavras & _PALAVRAS_CONFIRMACAO else set()
            duplicada_por_horario = bool(horario) and horario in horarios_confirmados
            if duplicada_por_contencao or duplicada_por_horario:
                continue
            frases_mantidas.append(frase)
            vistas.append(palavras)
            if horario:
                horarios_confirmados.append(horario)
        if frases_mantidas:
            blocos_finais.append(" ".join(frases_mantidas))
    return "\n\n".join(blocos_finais)


def _quebrar_em_paragrafos(texto: str) -> str:
    """Reagrupa o texto final em parágrafos curtos com linha em branco entre
    eles -- rede de segurança MECÂNICA pra regra de formato do prompt (ver
    `_montar_system_prompt`). Testado e confirmado: mesmo com a regra descrita
    como obrigatória + exemplo certo/errado explícito no prompt, o modelo
    continua devolvendo o texto todo num parágrafo só na maioria das vezes --
    mesmo padrão do que já acontecia com a regra de não repetir pergunta
    (ver `_texto_sem_repeticao`), então tratamos aqui em vez de insistir só
    no prompt.

    Ignora qualquer quebra de linha que já exista (trata o texto todo como
    uma sequência única de frases) e reagrupa: a última pergunta da mensagem
    (e qualquer frase depois dela) sempre fica num parágrafo próprio -- é o
    padrão mais comum (explicação + pergunta de fechamento); as frases
    anteriores são agrupadas de 2 em 2. Mensagem com 2 frases ou menos não é
    mexida (já é curta o suficiente pra não precisar de quebra)."""
    frases = [f.strip() for f in re.split(r"(?<=[.?!])\s+", texto.replace("\n", " ")) if f.strip()]
    if len(frases) <= 2:
        return " ".join(frases)

    indices_pergunta = [i for i, f in enumerate(frases) if f.endswith("?")]
    if indices_pergunta:
        corte = indices_pergunta[-1]
        corpo, final = frases[:corte], frases[corte:]
    else:
        corpo, final = frases, []

    paragrafos = [" ".join(corpo[i:i + 2]) for i in range(0, len(corpo), 2)]
    if final:
        paragrafos.append(" ".join(final))
    return "\n\n".join(p for p in paragrafos if p)


def conversar(
    db: Session,
    historico: list[dict],
    mensagem: str,
    *,
    telefone: str | None = None,
    deal_rd_id: str | None = None,
    modo_teste: bool = False,
    nome_lead: str | None = None,
) -> tuple[str, list[dict]]:
    """Roda um turno de conversa: adiciona `mensagem` ao `historico`, chama o
    Claude (repetindo o loop enquanto ele pedir ferramenta), devolve
    `(texto_da_resposta, historico_atualizado)`.

    `historico` e `historico_atualizado` são listas de dicts 100% serializáveis
    em JSON -- guarde e reenvie a cada turno pra manter o contexto da conversa
    (ver `scripts/conversar_com_agente.py` pra um exemplo de uso turno a turno).

    `nome_lead` (nome completo, opcional) personaliza o system prompt -- ver
    `_primeiro_nome_lead`."""
    cliente = _cliente()
    system_prompt = _montar_system_prompt(nome_lead=_primeiro_nome_lead(nome_lead))
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
            system=system_prompt,
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
            try:
                resultado = _executar_ferramenta(
                    db, chamada.name, chamada.input, telefone=telefone, deal_rd_id=deal_rd_id, modo_teste=modo_teste,
                )
            except Exception:  # noqa: BLE001 -- uma ferramenta falhando (ex: negociacao apagada
                # no RD, 404) nunca pode derrubar o turno inteiro e deixar o lead sem resposta.
                db.rollback()
                logger.exception(
                    "Agente: falha ao executar ferramenta %s (negociação %s).", chamada.name, deal_rd_id,
                )
                resultado = (
                    "[ERRO TECNICO ao executar essa acao -- nao foi possivel confirmar no CRM/agenda agora] "
                    "Avise o lead com naturalidade que houve um problema tecnico e que alguem vai confirmar "
                    "em breve, e chame encaminhar_para_humano."
                )
            resultados.append({"type": "tool_result", "tool_use_id": chamada.id, "content": resultado})
        mensagens.append({"role": "user", "content": resultados})

    texto_final = _quebrar_em_paragrafos(_texto_sem_repeticao(partes_texto))
    return texto_final, mensagens
