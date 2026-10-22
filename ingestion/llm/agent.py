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
  - consultar_horarios_disponiveis: consulta a agenda de verdade (Microsoft
    Graph) e devolve horarios REALMENTE livres pra SUGERIR ao lead. So
    leitura -- nunca escreve nada na agenda. Era a parte que ja funcionava
    bem antes do incidente de 2026-10-15 (ver abaixo); so a ESCRITA
    (confirmar/criar evento) que foi tirada do agente.
  - transferir_para_consultora: o lead topou marcar a reuniao/demonstracao,
    com ou sem ter aceitado um horario especifico sugerido por
    `consultar_horarios_disponiveis`. O agente NAO confirma a reuniao de
    verdade (ver decisao de 2026-10-15 abaixo) -- cria uma tarefa tipo
    'call' pro dono da negociacao (normalmente a consultora Miria), com o
    horario sugerido/aceito registrado no texto se houver, pra ela
    CONFIRMAR com o lead e finalizar o agendamento de verdade.
  - reagendar_reuniao: o lead JA TEM reuniao marcada (etapa "Reuniao
    Agendada", confirmada manualmente pela consultora) e pede pra mudar
    dia/horario (inclusive respondendo a um lembrete automatico). Atualiza
    `due_at` da tarefa tipo 'meeting' no RD (fonte de verdade lida por
    scripts/enviar_lembretes_reuniao.py) e, se houver um evento registrado
    em AgendaEventoMicrosoft pra essa negociacao, move o evento DE VERDADE
    no Outlook/Teams. Sem evento registrado (caso comum agora que a marcacao
    e manual), cai no fallback de avisar um humano pra ajustar.
  - encaminhar_para_humano: qualquer coisa que o agente NAO deve decidir
    sozinho -- ex: pedido de desconto/condicao fora da tabela padrao, duvida
    que a base de conhecimento nao cobre. Preco em si (ver tabela real em
    ingestion/llm/knowledge_base_seed.py) o agente informa direto desde
    2026-10-15 -- so NUNCA inventa um valor fora da tabela, NUNCA cita a
    faixa/intervalo em si (so os dois valores finais).

DECISAO DE 2026-10-15 -- por que nao CONFIRMA mais horario direto: o agente
tentava checar disponibilidade real E criar o evento no Microsoft Graph
dentro da propria conversa; a parte de CRIAR (POST nao-idempotente com
retry, ver ingestion/microsoft/client.py) gerava erro demais -- respostas
lentas/timeout tratadas como falha -- e fazia o agente "se perder" com o
lead, dizendo que deu erro enquanto a reuniao as vezes tinha sido criada de
verdade por baixo. A parte de CONSULTAR disponibilidade nunca foi o
problema (e so leitura, idempotente) -- por isso voltou: o agente ainda
sugere horario real, so nao escreve mais na agenda. `_criar_evento_na_agenda`
fica sem uso por enquanto (nao removida, intencional -- reaproveitar se a
confirmacao automatica for retomada no futuro).

Todas as ferramentas de acao (sinalizar_interesse, transferir_para_consultora,
reagendar_reuniao, encaminhar_para_humano) so mexem no CRM/agenda DE VERDADE
quando ha um `deal_rd_id` real E `modo_teste=False` -- uma conversa de teste
(sem negociacao real por tras) nunca aciona nada fora do proprio teste, mesmo
que o modelo "decida" chamar a ferramenta. Ver `conversar()` mais abaixo.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import anthropic
from sqlalchemy.orm import Session

from config.settings import settings
from database.models import AgendaEventoMicrosoft, CrmContact, CrmDeal, CrmTask, CrmUser
from ingestion.llm.pricing import registrar_chamada
from ingestion.llm.retrieval import buscar_contexto, montar_bloco_contexto
from ingestion.rd_crm.actions import (
    atualizar_prazo_tarefa,
    criar_negociacao_com_contato,
    criar_tarefa,
    mover_negociacao_para_etapa,
)

logger = logging.getLogger(__name__)

NOME_AGENTE = "TEO"
_FUSO_BRASIL = ZoneInfo("America/Sao_Paulo")
_DURACAO_REUNIAO_MINUTOS = 60

# Guardrail de horario comercial (pedido do dono do produto em 2026-09-18):
# reuniao so pode ser marcada de segunda a sexta, entre 09:00 e 17:00 (horario
# de Brasilia) -- o expediente real vai das 08:00 as 18:00, mas com 1h de
# folga em cada ponta pra nao marcar em cima da hora de abrir/fechar. Tambem
# nao pode em cima do almoco (12:00-13:30).
_EXPEDIENTE_INICIO = time(9, 0)
_EXPEDIENTE_FIM = time(17, 0)
_ALMOCO_INICIO = time(12, 0)
_ALMOCO_FIM = time(13, 30)

# Horarios padrao que o agente oferece PROATIVAMENTE (ver
# `_proximos_horarios_livres`) -- ja nascem fora da janela de almoco de
# proposito, e cada um tem folga de 1h+ do seguinte (reuniao dura
# `_DURACAO_REUNIAO_MINUTOS`).
_SLOTS_PADRAO = [time(9, 0), time(10, 30), time(14, 0), time(15, 30)]


_DIAS_SEMANA_PT = [
    "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
    "sexta-feira", "sábado", "domingo",
]


def _horario_no_expediente(horario: datetime) -> tuple[bool, str | None]:
    """Confere o guardrail de horario comercial (ver _EXPEDIENTE_*). Devolve
    (valido, motivo_se_invalido) -- usado tanto por `_proximos_horarios_livres`
    (pra nunca SUGERIR um horario fora do expediente) quanto, antes de mexer
    no CRM/agenda de verdade, em `reagendar_reuniao`."""
    if horario.weekday() >= 5:  # sabado=5, domingo=6
        return False, "não marcamos reunião aos fins de semana"
    if horario.time() < _EXPEDIENTE_INICIO or horario.time() > _EXPEDIENTE_FIM:
        return False, "só marcamos reunião em horário comercial, entre 09:00 e 17:00"
    # Com reuniao de 1h (_DURACAO_REUNIAO_MINUTOS), um horario que comeca DENTRO
    # do expediente ainda pode TERMINAR depois das 17:00 (ex: 16:30) -- checa o
    # fim tambem, nao so o inicio.
    fim = (horario + timedelta(minutes=_DURACAO_REUNIAO_MINUTOS)).time()
    if fim > _EXPEDIENTE_FIM:
        return False, f"esse horário terminaria depois das {_EXPEDIENTE_FIM.strftime('%H:%M')}, fora do expediente"
    # Overlap [inicio, fim) x [almoco_inicio, almoco_fim) -- nao basta checar
    # so o INICIO (mesmo motivo do fim x expediente acima): uma reuniao de 1h
    # comecando as 11:15 termina 12:15, invadindo o almoco, mesmo comecando
    # antes dele.
    if horario.time() < _ALMOCO_FIM and fim > _ALMOCO_INICIO:
        return False, "esse horário cai no intervalo de almoço (12:00 às 13:30)"
    return True, None


def _parse_horario_brasil(horario_iso: str) -> datetime:
    """Interpreta `horario_iso` (vindo do modelo) SEMPRE como horário de
    Brasília: se a string já tiver um fuso (qualquer um), CONVERTE de verdade
    pra America/Sao_Paulo (`.astimezone`, não só troca a etiqueta); se vier
    sem fuso, assume que os números já são de Brasília. Usar isso em TODO
    lugar que lê `horario_iso`/`novo_horario_iso` do modelo.

    Antes, só o guardrail de horário comercial convertia de verdade
    (`.astimezone`); a consulta de disponibilidade e a criação do evento só
    trocavam a etiqueta quando vinha sem fuso (`.replace(tzinfo=...)`), sem
    reconverter quando vinha COM um fuso diferente -- os dois lados podiam
    divergir se o modelo alguma vez mandasse um offset que não fosse -03:00.
    É a mesma classe do incidente de 2026-09-19 (reunião de 14h virou 11h na
    agenda por causa do fuso), agora fechada de vez com uma única fonte de
    verdade pra essa conversão."""
    momento = datetime.fromisoformat(horario_iso)
    if momento.tzinfo is None:
        return momento.replace(tzinfo=_FUSO_BRASIL)
    return momento.astimezone(_FUSO_BRASIL)


def _primeiro_nome_lead(nome_completo: str | None) -> str | None:
    if nome_completo and nome_completo.strip():
        return nome_completo.strip().split()[0].capitalize()
    return None


# Parte ESTATICA do system prompt -- nunca muda entre chamadas (nem entre
# conversas diferentes), por isso fica separada da parte dinamica (data/hora,
# nome do lead) e ganha `cache_control` em `conversar()`: assim a Claude nao
# recalcula essas ~1000+ tokens (mais as `_TOOLS`, que vem logo antes no
# request) do zero em toda mensagem de toda conversa -- so paga o preco cheio
# na primeira vez, as demais leem do cache (~90% mais barato). Juntar a data
# atual ou o nome do lead aqui de volta quebraria o cache silenciosamente
# (muda a toda mensagem/conversa) sem dar nenhum erro -- ver
# `_montar_contexto_dinamico` pra isso.
_SYSTEM_PROMPT_BASE = f"""Você é {NOME_AGENTE}, o agente de vendas da Máquina.ISP -- uma solução de agentes de IA para provedores de internet (ISPs). Você atende pelo WhatsApp leads que chegaram através de anúncio ou do site, interessados em conhecer o produto.

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
- REGRA DE PREÇO: quando o lead perguntar sobre mensalidade/preço/investimento, chame `consultar_base_conhecimento` pela tabela de preços e informe SÓ a mensalidade e o setup correspondentes ao número de assinantes que ele já te disse -- NUNCA cite a faixa/intervalo em si (nunca diga algo como "você entra na faixa de 10.000 a 14.999" ou "pra quem tem entre X e Y assinantes"), SÓ os dois valores finais, como se fossem o preço dele, não o de uma categoria. Se ele ainda não disse quantos assinantes tem, pergunte isso primeiro -- sem isso você não sabe qual valor informar, e NUNCA chuta ou arredonda de cabeça.
  Seja objetivo: o valor, depois UMA frase de gancho pra reunião -- nada de parágrafo longo explicando tudo de uma vez. O gancho é real (nunca um desconto inventado -- nunca prometa "pode ser menor"): esse é o valor de tabela, e forma de pagamento, prazo de contrato e o timing do setup são o que a consultora ajusta junto com o lead na reunião -- isso é o que muda o número final pra quem fecha. Combine com os 45 dias de teste sem custo (tira o risco de decidir sem ter visto funcionando) e já puxe pra reunião, sem enrolar.
  Exemplo de resposta CERTA (direto, sem citar faixa, gancho real): "A mensalidade fica R$ 3.441/mês, com setup de R$ 7.500 cobrado uma vez só. Esse é o valor de tabela -- forma de pagamento e prazo são o que a consultora ajusta com você na reunião, e tem 45 dias de teste sem custo antes de qualquer cobrança começar. Faz sentido eu já encaminhar?"
  NUNCA informe um valor que não esteja na tabela recuperada pela ferramenta.
- FLUXO DE REUNIÃO EM TRÊS ETAPAS -- não pule etapa, e não pule pra primeira sem antes entender o cenário (ver regra acima): (1) assim que o lead demonstrar interesse real em avançar (topar conhecer melhor, topar uma reunião, pedir pra "ver funcionando"), chame `sinalizar_interesse`; (2) em seguida chame `consultar_horarios_disponiveis` e apresente 2-3 das opções REAIS que ela devolver (ex: "Tenho sugestão de segunda às 9h ou 14h, ou terça às 10h30 -- alguma dessas funciona?") -- NUNCA pergunte "manhã ou tarde funciona melhor?" de forma vaga, e NUNCA chute um horário sem consultar antes; (3) quando o lead aceitar uma das opções (ou pedir um dia/horário que você ainda não checou -- aí chame `consultar_horarios_disponiveis` de novo com `a_partir_de_iso` nesse dia antes de aceitar cego), chame `transferir_para_consultora` com esse `horario_iso`. Se o lead topar marcar mas sem aceitar nenhum horário específico, chame `transferir_para_consultora` sem `horario_iso`.
- IMPORTANTE: você SUGERE horário com base na agenda real, mas NUNCA confirma a reunião de verdade -- isso é sempre com a consultora. Depois de chamar `transferir_para_consultora`, a mensagem pro lead deixa claro que o horário (quando houver) ainda vai ser CONFIRMADO pela consultora, nunca que já está marcado/fechado -- ex: "Combinado, vou deixar [dia/hora] registrado e a [nome da consultora] confirma com você em breve." Nunca diga "está marcado" ou "sua reunião é dia X" sem a consultora ter confirmado de verdade.
- NÃO insista na reunião em toda mensagem. Depois de já ter convidado o lead pra marcar (via `sinalizar_interesse` ou já tendo transferido pra consultora), responda as próximas perguntas dele normalmente, SEM reanexar "bora marcar?" de novo. Pedir mais detalhe técnico ou um exemplo (ex: "como funciona?", "me dá um exemplo", "manda com botão?") é o lead ainda ENTENDENDO o produto, NÃO é sinal de avanço -- responda a dúvida e siga em frente sem repetir o convite. Tirar 2 ou 3 dúvidas técnicas seguidas sem repetir o convite é o comportamento CERTO, não uma falha. Só retome o convite quando o lead sinalizar avanço de verdade (pergunta de preço, "quero ver funcionando", "como contrato", foco em fechar) ou quando ele parecer sem mais perguntas novas.
- Se o lead JÁ TEM uma reunião marcada (a consultora combinou um horário com ele, ou às vezes você vai estar respondendo um lembrete automático que você mesmo mandou antes) e pedir pra mudar o dia/horário, chame `reagendar_reuniao` com o novo horário.
- Quando você usa uma ferramenta no meio de uma resposta, o texto de antes e o texto de depois do resultado da ferramenta formam UMA ÚNICA mensagem pro lead, mandada de uma vez -- nunca repita, na parte de depois, uma pergunta ou frase que você já fez na parte de antes (ex: não repita os mesmos horários de novo só porque chamou uma ferramenta no meio). ANTES DE MANDAR, releia o texto completo (antes + depois da ferramenta): se a mesma pergunta aparecer duas vezes, tire uma."""


def _montar_contexto_dinamico(nome_lead: str | None = None, *, sem_cadastro: bool = False) -> str:
    """Parte DINAMICA do system prompt -- data/hora atual (sem isso o modelo
    nao tem como saber que dia e hoje pra calcular "amanha de manha" etc de
    forma confiavel na hora de preencher `horario_iso`) e o nome do lead, se
    conhecido. Fica de fora de `_SYSTEM_PROMPT_BASE` de proposito, pra nao
    invalidar o cache dele -- ver comentario la. Nome do dia da semana
    escrito na mao (nao via `%A`) porque isso depende do locale do sistema
    operacional, que aqui fica em ingles por padrao."""
    agora = datetime.now(_FUSO_BRASIL)
    dia_semana = _DIAS_SEMANA_PT[agora.weekday()]
    linha_nome = (
        f'\nVocê está falando com {nome_lead}. Use o nome dele de vez em quando, com naturalidade -- não em toda mensagem, isso soa forçado.'
        if nome_lead else ""
    )
    # Sinaliza o lead de QR code (chegou sem nenhum card no CRM). Vai na parte
    # DINAMICA porque muda de lead pra lead -- e deixa de valer assim que
    # `criar_negociacao_no_crm` roda e o card passa a existir.
    linha_cadastro = (
        "\nESTE LEAD AINDA NÃO TEM CADASTRO no nosso sistema -- ele chegou por um QR code, "
        "não por anúncio. Antes de falar de reunião, colete na conversa, aos poucos e sem "
        "parecer formulário: nome do provedor, nome dele, quantidade de assinantes e e-mail. "
        "NUNCA peça o telefone (já temos, é este número). Ao pedir o e-mail, dê um motivo "
        "que faça sentido pro lead (mandar o material, o resumo do que conversaram), NUNCA "
        "\"pra finalizar seu cadastro\" -- o lead não tem que saber que existe cadastro. "
        "E NUNCA prometa que VOCÊ vai mandar material/e-mail: você não envia e-mail nenhum, "
        "quem envia é a consultora depois. Peça como o canal pra ela te mandar o material e a "
        "proposta (ex: \"qual seu e-mail? é por onde a consultora te manda o material depois\"). "
        "Com as quatro respostas em mãos, "
        "chame `criar_negociacao_no_crm`. Responda as dúvidas dele normalmente enquanto isso -- "
        "não trave a conversa esperando os dados."
        if sem_cadastro else ""
    )
    return (
        f"Hoje é {dia_semana}, {agora:%d/%m/%Y}, agora são {agora:%H:%M} (horário de Brasília). "
        f'Use isso pra calcular datas relativas ("amanhã", "sexta-feira", etc) corretamente.{linha_nome}{linha_cadastro}'
    )


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
        "name": "criar_negociacao_no_crm",
        "description": (
            "Chame SÓ quando o contexto disser que este lead AINDA NÃO TEM CADASTRO e "
            "você já tiver coletado na conversa: nome da empresa, nome da pessoa, "
            "quantidade de assinantes e e-mail. Cria o cadastro do provedor no nosso "
            "sistema. O telefone é pego automaticamente do próprio número que está "
            "conversando -- NUNCA pergunte o telefone. Chame uma única vez por lead: "
            "depois de criado, o cadastro já existe e as outras ferramentas passam a "
            "funcionar normalmente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nome_empresa": {
                    "type": "string",
                    "description": "Nome do provedor como o lead falou (razão social ou nome fantasia).",
                },
                "nome_contato": {
                    "type": "string",
                    "description": "Nome da pessoa que está conversando.",
                },
                "quantidade_assinantes": {
                    "type": "string",
                    "description": "Quantidade de assinantes como o lead respondeu (ex: '3000', '15 mil').",
                },
                "email": {"type": "string", "description": "E-mail informado pelo lead."},
            },
            "required": ["nome_empresa", "nome_contato", "quantidade_assinantes", "email"],
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
        "name": "consultar_horarios_disponiveis",
        "description": (
            "Consulta a agenda de verdade (Microsoft Graph) e devolve os próximos horários "
            "REALMENTE livres pra SUGERIR ao lead -- já pula fim de semana, horário fora do "
            "expediente e horário de almoço automaticamente. Chame antes de sugerir qualquer "
            "dia/horário (inclusive quando o lead já chegar sugerindo um dia, tipo 'pode ser "
            "segunda') -- nunca pergunte 'manhã ou tarde funciona melhor?' de forma vaga nem "
            "chute um horário sem consultar antes. Isso só SUGERE -- não marca nada de verdade; "
            "quem confirma é a consultora depois (ver `transferir_para_consultora`)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "a_partir_de_iso": {
                    "type": "string",
                    "description": (
                        "Data (só a data, formato YYYY-MM-DD) a partir da qual buscar horários -- "
                        "use quando o lead já mencionou um dia preferido (ex: 'semana que vem', "
                        "'segunda'). Omita pra buscar a partir de agora."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "transferir_para_consultora",
        "description": (
            "Chame quando o lead topar marcar a reunião/demonstração -- com ou sem ter "
            "aceitado um horário específico dentre os sugeridos por "
            "`consultar_horarios_disponiveis`. O agente NÃO confirma a reunião de verdade "
            "(não cria evento na agenda real -- gerava erro demais tentando bater com a "
            "agenda de verdade). Em vez disso, registra o horário sugerido/aceito (se "
            "houver) numa tarefa pra nossa consultora de negócios CONFIRMAR com o lead e "
            "finalizar o agendamento de verdade."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "horario_iso": {
                    "type": "string",
                    "description": (
                        "Se o lead aceitou um horário específico dentre os sugeridos, formato "
                        "ISO-8601 com offset de fuso (ex: '2026-09-08T09:00:00-03:00'). Omita se "
                        "o lead só topou agendar sem ter escolhido horário ainda."
                    ),
                },
                "resumo": {
                    "type": "string",
                    "description": "Resumo curto do que o lead quer ver na demonstração / contexto combinado.",
                },
            },
            "required": ["resumo"],
        },
    },
    {
        "name": "reagendar_reuniao",
        "description": (
            "Chame quando o lead JÁ TEM uma reunião marcada (negociação na etapa "
            "'Reunião Agendada') e pedir pra mudar o dia/horário -- inclusive em resposta "
            "a um lembrete automático de reunião. Atualiza o horário da reunião no CRM."
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
            "Chame quando o lead pedir desconto/condição especial fora da tabela de preços "
            "padrão, ou fizer uma pergunta que a base de conhecimento não cobre. Preço em si "
            "(mensalidade/setup da tabela) você informa direto, não precisa encaminhar só por "
            "isso. Registra a necessidade de um humano assumir esse ponto da conversa."
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

        inicio = _parse_horario_brasil(horario_iso)
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


def _proximos_horarios_livres(
    owner: CrmUser, *, a_partir_de: date | None = None, max_opcoes: int = 4,
) -> list[datetime]:
    """Varre os proximos dias uteis a partir de `a_partir_de` (ou de agora,
    se omitido) testando os `_SLOTS_PADRAO` contra a agenda de verdade
    (`_horario_disponivel_nas_agendas`) ate achar `max_opcoes` horarios
    realmente livres. Pula fim de semana, horario que ja passou e o
    guardrail de expediente/almoco automaticamente -- pedido do dono do
    produto em 2026-09-18: o agente estava propondo "amanha" sem checar se
    caia num fim de semana, e chutando horario por horario sem consultar a
    agenda antes, deixando o lead escolher um horario que so descobria
    ocupado depois. Limite de 14 dias corridos de busca pra nunca rodar pra
    sempre num periodo muito cheio."""
    agora = datetime.now(_FUSO_BRASIL)
    dia = a_partir_de or agora.date()
    encontrados: list[datetime] = []
    for _ in range(14):
        for hora in _SLOTS_PADRAO:
            candidato = datetime.combine(dia, hora, tzinfo=_FUSO_BRASIL)
            if candidato <= agora:
                continue
            dentro_do_expediente, _ = _horario_no_expediente(candidato)
            if not dentro_do_expediente:
                continue
            disponivel, _ = _horario_disponivel_nas_agendas(owner, candidato.isoformat())
            if disponivel:
                encontrados.append(candidato)
                if len(encontrados) >= max_opcoes:
                    return encontrados
        dia = dia + timedelta(days=1)
    return encontrados


def _formatar_horarios_livres(horarios: list[datetime]) -> str:
    return "\n".join(f"{_DIAS_SEMANA_PT[h.weekday()]} ({h:%d/%m}) às {h:%H:%M}" for h in horarios)


def _criar_evento_na_agenda(db: Session, *, deal_rd_id: str, owner: CrmUser, horario_iso: str, resumo: str) -> str | None:
    """SEM USO desde 2026-10-15 (nenhuma ferramenta chama mais isso -- ver
    decisao no topo do arquivo e `transferir_para_consultora`). Mantida de
    proposito, nao removida: reaproveitar aqui se o auto-agendamento pela
    propria conversa for retomado no futuro, ja validada contra o Graph real.

    Tenta criar o evento de verdade na agenda do dono da negociacao via
    Microsoft Graph. Convida o lead (quando o e-mail dele e conhecido) e,
    se houver uma segunda pessoa mapeada pra esse dono (ver
    `_segunda_agenda_cruzada`), convida ela tambem -- e a mesma pessoa cuja
    agenda ja foi checada em `_horario_disponivel_nas_agendas`, entao ela
    precisa estar de fato incluida no evento, nao so ter a disponibilidade
    consultada. Devolve o link da reuniao do Teams se der certo, ou None se
    falhar de verdade (credencial, rede, horario invalido) -- antes de
    desistir, RECONFERE no Graph se o evento nao foi criado mesmo assim (ver
    MicrosoftCalendarClient.criar_evento: a chamada de criacao NAO tem retry
    automatico de proposito, mas uma resposta lenta/cortada ainda pode
    mascarar um sucesso real). Erro aqui nunca deve quebrar o fluxo
    principal -- quem chama (`_executar_ferramenta`) cai pro fallback de
    criar uma tarefa manual, e qualquer excecao nao prevista aqui ainda e
    pega pelo guard generico em `conversar()`."""
    if not owner.email:
        return None

    inicio = _parse_horario_brasil(horario_iso)
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
    # do evento, nao no titulo, que ficava enorme e dificil de ler na agenda.
    # Calculado ANTES do try porque `buscar_evento_no_horario` precisa do
    # mesmo assunto pra reconhecer o evento, se precisar ser usado.
    nome_cliente = (deal.name if deal and deal.name else resumo).strip()
    assunto = f"{nome_cliente} + Máquina ISP"[:250]

    try:
        evento = client.criar_evento(
            email_organizador=owner.email,
            assunto=assunto,
            inicio=inicio,
            fim=fim,
            participantes=participantes or None,
            corpo=f"Reunião marcada automaticamente pelo agente {NOME_AGENTE}.<br>{resumo}",
        )
    except Exception:  # noqa: BLE001 -- ver docstring de MicrosoftCalendarClient.criar_evento:
        # a chamada pode ter CRIADO o evento de verdade no Graph mesmo tendo
        # lancado excecao aqui (resposta lenta/cortada depois do Graph ja ter
        # processado) -- reconfere antes de reportar falha, pra nunca dizer
        # pro lead que nao deu certo quando, por baixo, deu (incidente real
        # em 2026-10-13: reuniao criada as 23:41:10, mas o agente continuou
        # dizendo "problema tecnico" e oferecendo outros horarios).
        logger.exception(
            "Agente: falha ao criar evento na agenda pra negociação %s -- reconferindo antes de desistir.",
            deal_rd_id,
        )
        try:
            evento = client.buscar_evento_no_horario(owner.email, inicio, fim, assunto)
        except Exception:  # noqa: BLE001 -- reconferencia tambem pode falhar, nao pode quebrar o fluxo
            logger.exception("Agente: falha ao reconferir evento pra negociação %s.", deal_rd_id)
            evento = None
        if not evento:
            return None

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
        inicio = _parse_horario_brasil(novo_horario_iso)
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
    estado: dict | None = None,
) -> str:
    """`estado` e o canal de volta pro `conversar`: `criar_negociacao_no_crm`
    grava ali o rd_id da negociacao recem-criada pra que as ferramentas
    seguintes DO MESMO TURNO ja ajam sobre ela (sem isso, o lead que manda
    "meus dados sao X, e ja quero agendar" numa mensagem so teria o cadastro
    criado e o agendamento perdido, porque `deal_rd_id` ainda seria None)."""
    if nome == "consultar_base_conhecimento":
        chunks = buscar_contexto(db, entrada["pergunta"], limite=4)
        return montar_bloco_contexto(chunks)

    if nome == "criar_negociacao_no_crm":
        nome_empresa = (entrada.get("nome_empresa") or "").strip()
        nome_contato = (entrada.get("nome_contato") or "").strip()
        assinantes = (entrada.get("quantidade_assinantes") or "").strip()
        email = (entrada.get("email") or "").strip()

        if deal_rd_id:
            return (
                "Esse lead JÁ TEM cadastro -- não crie outro. NÃO comente isso com o lead "
                "(nada de \"já te encontrei no sistema\"): é interno. Apenas siga a conversa "
                "normalmente, usando as outras ferramentas."
            )
        faltando = [
            rotulo for rotulo, valor in (
                ("nome da empresa", nome_empresa), ("nome da pessoa", nome_contato),
                ("quantidade de assinantes", assinantes), ("e-mail", email),
            ) if not valor
        ]
        if faltando:
            return (
                f"Faltou coletar: {', '.join(faltando)}. Pergunte ao lead de forma natural "
                "e só então chame esta ferramenta de novo."
            )
        if modo_teste or not telefone:
            return (
                "[MODO TESTE -- nada foi criado no CRM] Cadastro seria criado pra "
                f"{nome_empresa} ({nome_contato}, {assinantes} assinantes, {email}). "
                "Siga a conversa como se tivesse dado certo, SEM comentar com o lead que "
                "criou cadastro/registro -- isso é interno."
            )

        negociacao = criar_negociacao_com_contato(
            db,
            nome_empresa=nome_empresa,
            nome_contato=nome_contato,
            telefone=telefone,
            owner_rd_id=settings.whatsapp_agent_owner_padrao_rd_id,
            pipeline_rd_id=settings.rd_pipeline_maquina_isp_qualificacao_rd_id,
            stage_rd_id=settings.rd_stage_primeira_conexao_rd_id,
            source_rd_id=settings.rd_source_qrcode_rd_id,
            email=email,
            quantidade_assinantes=assinantes,
        )
        if estado is not None:
            estado["deal_rd_id"] = negociacao["id"]
        return (
            "Cadastro criado com sucesso. NÃO comente que criou cadastro, registro ou card -- "
            "isso é interno. Apenas agradeça as informações com naturalidade e siga a conversa "
            "de onde parou."
        )

    if nome == "sinalizar_interesse":
        resumo = entrada.get("resumo", "")
        if modo_teste or not deal_rd_id:
            return f"[MODO TESTE -- nada foi alterado no CRM] Negociação seria movida pra 'Interesse Identificado'. Resumo: {resumo}"
        mover_negociacao_para_etapa(db, deal_rd_id, settings.rd_stage_interesse_identificado_rd_id)
        return "Interesse registrado no CRM com sucesso."

    if nome == "consultar_horarios_disponiveis":
        a_partir_de_iso = entrada.get("a_partir_de_iso") or ""
        if modo_teste or not deal_rd_id:
            return (
                "[MODO TESTE -- sem negociação real pra consultar agenda] Simule 2-3 horários "
                "plausíveis dentro do expediente (seg-sex, 09:00-17:00, fora do almoço 12:00-13:30) "
                "e proponha como se fossem reais."
            )
        owner = _dono_da_negociacao(db, deal_rd_id)
        if not owner or not _pode_usar_calendario():
            return (
                "Integração de agenda não disponível agora -- pergunte ao lead qual dia/período "
                "prefere e chame transferir_para_consultora quando ele topar."
            )
        a_partir_de = None
        if a_partir_de_iso:
            try:
                a_partir_de = date.fromisoformat(a_partir_de_iso[:10])
            except ValueError:
                a_partir_de = None
        horarios = _proximos_horarios_livres(owner, a_partir_de=a_partir_de)
        if not horarios:
            return (
                "Não achei nenhum horário livre nos próximos dias -- avise o lead com naturalidade "
                "e peça pra ele sugerir um dia específico, ou chame transferir_para_consultora mesmo assim."
            )
        return (
            "Horários realmente livres na agenda (ainda é SUGESTÃO, não confirma nada por si só):\n"
            + _formatar_horarios_livres(horarios)
        )

    if nome == "transferir_para_consultora":
        resumo = entrada.get("resumo", "")
        horario_iso = entrada.get("horario_iso") or ""

        if modo_teste or not deal_rd_id:
            return (
                "[MODO TESTE -- nada foi alterado no CRM] Em produção, uma tarefa seria criada "
                f"pra nossa consultora de negócios confirmar com o lead. Horário sugerido/aceito: "
                f"{horario_iso or '(nenhum ainda)'}. Resumo: {resumo}"
            )

        owner = _dono_da_negociacao(db, deal_rd_id)
        if not owner:
            return (
                "Não encontrei quem é o responsável por essa negociação pra criar a tarefa -- "
                "avise o lead com naturalidade que alguém do nosso time comercial vai entrar em "
                "contato em breve pra agendar, e chame encaminhar_para_humano."
            )

        nome_consultora = _primeiro_nome_lead(owner.name) or "nossa consultora de negócios"

        # tipo="call", NUNCA "meeting" -- scripts/enviar_lembretes_reuniao.py le
        # tarefa tipo 'meeting' como se fosse uma reuniao JA CONFIRMADA num
        # horario certo (due_at = horario da reuniao) pra disparar lembrete
        # automatico; mesmo com um horario_iso aceito aqui, isso ainda e so
        # SUGESTAO -- nada foi confirmado na agenda de verdade (nao chamamos
        # _criar_evento_na_agenda), entao marcar como 'meeting' dispararia um
        # lembrete de reuniao falso antes da consultora sequer ter confirmado.
        if horario_iso:
            horario_sugerido = _parse_horario_brasil(horario_iso)
            texto_horario = f"{_DIAS_SEMANA_PT[horario_sugerido.weekday()]} ({horario_sugerido:%d/%m}) às {horario_sugerido:%H:%M}"
            texto_tarefa = (
                f"Lead aceitou {texto_horario} pra demonstração (SUGESTÃO, ainda não confirmado na "
                f"agenda) -- CONFIRMAR de verdade com o lead o quanto antes. Resumo: {resumo}"
            )
        else:
            texto_tarefa = f"Lead topou agendar demonstração, sem horário aceito ainda -- ligar/chamar no WhatsApp pra combinar. Resumo: {resumo}"

        criar_tarefa(
            db,
            deal_rd_id,
            tipo="call",
            texto=texto_tarefa,
            responsavel_rd_id=owner.rd_id,
            criado_por_rd_id=owner.rd_id,
            prazo=datetime.now(_FUSO_BRASIL) + timedelta(hours=2),
        )

        if horario_iso:
            return (
                f"Tarefa criada pra {nome_consultora} confirmar {texto_horario} com o lead. Avise "
                f"o lead com naturalidade que a {nome_consultora}, nossa consultora de negócios, "
                "vai confirmar esse horário com ele em breve -- NUNCA diga que já está "
                "marcado/confirmado, diga que ela vai confirmar."
            )
        return (
            f"Tarefa criada pra {nome_consultora} ligar e combinar o horário. Avise o lead com "
            f"naturalidade que a {nome_consultora}, nossa consultora de negócios, vai entrar em "
            "contato em breve pra agendar o melhor horário pra demonstração do produto."
        )

    if nome == "reagendar_reuniao":
        novo_horario_iso = entrada.get("novo_horario_iso", "")
        resumo = entrada.get("resumo", "")

        novo_horario_pedido = _parse_horario_brasil(novo_horario_iso)
        dentro_do_expediente, motivo_fora = _horario_no_expediente(novo_horario_pedido)
        if not dentro_do_expediente:
            return (
                f"[FORA DO HORÁRIO COMERCIAL -- {motivo_fora}, nada foi alterado no CRM/agenda] "
                "Explique isso pro lead com naturalidade e peça um horário dentro do expediente "
                "(segunda a sexta, 09:00 às 17:00)."
            )

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

        novo_horario = _parse_horario_brasil(novo_horario_iso)

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
        # ja passa a valer pro novo horario sozinho. Se por acaso essa
        # negociacao tiver um evento registrado em AgendaEventoMicrosoft
        # (marcacao antiga, de antes de 2026-10-15), move o evento DE VERDADE
        # no Outlook/Teams; senao (caso comum agora, reuniao marcada
        # manualmente pela consultora), cai no fallback de avisar um humano
        # pra ajustar.
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
    (ver `_SYSTEM_PROMPT_BASE`), mas na pratica ainda escapa -- sobretudo
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
    `_SYSTEM_PROMPT_BASE`). Testado e confirmado: mesmo com a regra descrita
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
    # `system` como lista de 2 blocos, nao uma string so: o primeiro (estatico,
    # identico em toda chamada) leva `cache_control` e cobre tambem as
    # `_TOOLS` (ordem do request e tools -> system -> messages, entao tudo
    # antes do breakpoint entra no mesmo prefixo cacheado); o segundo (data/
    # hora + nome do lead) muda a cada chamada e fica de fora do cache de
    # proposito -- ver `_SYSTEM_PROMPT_BASE` e `_montar_contexto_dinamico`.
    system_prompt = [
        {"type": "text", "text": _SYSTEM_PROMPT_BASE, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": _montar_contexto_dinamico(
            nome_lead=_primeiro_nome_lead(nome_lead), sem_cadastro=deal_rd_id is None,
        )},
    ]
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
                # `estado` deixa `criar_negociacao_no_crm` devolver o rd_id da
                # negociacao que acabou de criar, e a releitura logo abaixo faz
                # as ferramentas seguintes (deste turno e dos proximos) ja
                # agirem sobre ela.
                estado: dict = {}
                resultado = _executar_ferramenta(
                    db, chamada.name, chamada.input, telefone=telefone, deal_rd_id=deal_rd_id,
                    modo_teste=modo_teste, estado=estado,
                )
                deal_rd_id = estado.get("deal_rd_id") or deal_rd_id
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
