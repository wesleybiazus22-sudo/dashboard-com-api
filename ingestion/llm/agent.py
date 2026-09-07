"""
Motor de conversa do agente de atendimento (TEO). Recebe o historico de uma
conversa + a mensagem nova do lead, chama o Claude com RAG (base de
conhecimento, ver ingestion/llm/retrieval.py) e ferramentas de acao no CRM, e
devolve a resposta.

Arquitetura: loop de tool-use da API do Claude direto (sem framework de
agente) -- poucas ferramentas, bem definidas:
  - consultar_base_conhecimento: RAG, sempre real, so leitura.
  - marcar_reuniao: o lead topou marcar a reuniao/demonstracao.
  - encaminhar_para_humano: qualquer coisa que o agente NAO deve decidir
    sozinho -- acima de tudo, negociacao de preco/desconto (regra explicita
    do dono do produto: o agente nunca inventa nem estima mensalidade).

`marcar_reuniao` e `encaminhar_para_humano` so mexem no CRM DE VERDADE quando
ha um `deal_rd_id` real E `modo_teste=False` -- uma conversa de teste (sem
negociacao real por tras) nunca aciona nada fora do proprio teste, mesmo que
o modelo "decida" chamar a ferramenta. Ver `conversar()` mais abaixo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import anthropic
from sqlalchemy.orm import Session

from config.settings import settings
from database.models import CrmDeal
from ingestion.llm.pricing import registrar_chamada
from ingestion.llm.retrieval import buscar_contexto, montar_bloco_contexto
from ingestion.rd_crm.actions import criar_tarefa, mover_negociacao_para_etapa

NOME_AGENTE = "TEO"

SYSTEM_PROMPT = f"""Você é {NOME_AGENTE}, o agente de vendas da Máquina.ISP -- uma solução de agentes de IA para provedores de internet (ISPs). Você atende pelo WhatsApp leads que chegaram através de anúncio ou do site, interessados em conhecer o produto.

SEU OBJETIVO: conduzir a conversa até o lead aceitar marcar uma reunião/demonstração. Você não fecha venda por texto -- o objetivo é a reunião marcada, não o contrato assinado.

COMO SE COMPORTAR:
- Tom direto, humano, consultivo -- nunca robótico nem com respostas de manual. Frases curtas, como numa conversa real de WhatsApp (não escreva parágrafos longos).
- Use a ferramenta `consultar_base_conhecimento` sempre que precisar de um fato sobre o produto (o que cada agente faz, como funciona a implementação, integrações, teste grátis, etc.) antes de responder -- nunca invente ou "chute" uma informação sobre o produto.
- Se a base de conhecimento não trouxer a resposta pra alguma pergunta, admita com naturalidade que vai confirmar, e chame `encaminhar_para_humano`. Não invente.
- REGRA INEGOCIÁVEL: você NUNCA informa, estima ou sugere um valor de mensalidade/preço, mesmo que o lead insista, peça "só uma faixa", ou diga que só decide sabendo o preço. Toda vez que o lead tocar em preço/valor/desconto/condição de pagamento: (1) diga com naturalidade que o valor é justamente o que se esclarece NA REUNIÃO com um consultor, olhando o tamanho e o cenário do provedor dele -- não é algo que se define por mensagem; (2) pode adiantar que tem 60 dias de teste sem custo de implementação; (3) chame `encaminhar_para_humano`; e (4) use isso como o gancho natural pra propor a reunião (ou reforçar a que já foi proposta) -- a reunião não é uma coisa separada de "alguém vai te chamar", ela É onde a resposta de preço está. Nunca deixe a pergunta de preço "no ar" tipo só "vou chamar o time comercial" sem amarrar isso à reunião.
- Assim que o lead demonstrar interesse real em avançar (topar conhecer melhor, topar uma reunião, pedir pra "ver funcionando"), proponha ativamente marcar a reunião -- não espere ele pedir. Ofereça horários de forma simples (ex: "amanhã de manhã ou à tarde funciona melhor pra você?") e, quando ele confirmar, chame `marcar_reuniao`.
- Nunca chame `marcar_reuniao` sem o lead ter confirmado explicitamente um horário ou intenção clara de agendar.
- Pode fazer perguntas leves de qualificação (quantos assinantes tem o provedor, qual ERP usa) pra a reunião já chegar com contexto, mas sem parecer um formulário.
- Quando você usa uma ferramenta no meio de uma resposta, o texto de antes e o texto de depois do resultado da ferramenta formam UMA ÚNICA mensagem pro lead, mandada de uma vez -- nunca repita, na parte de depois, uma pergunta ou frase que você já fez na parte de antes (ex: não pergunte "manhã ou tarde?" de novo só porque chamou uma ferramenta no meio)."""

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
        "name": "marcar_reuniao",
        "description": (
            "Chame quando o lead confirmar explicitamente que quer marcar a reunião/"
            "demonstração (aceitou um horário ou pediu pra agendar). Registra a "
            "intenção no CRM."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "resumo": {
                    "type": "string",
                    "description": "Resumo curto do combinado (horário sugerido, pontos de interesse do lead).",
                },
            },
            "required": ["resumo"],
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


def _dono_da_negociacao(db: Session, deal_rd_id: str) -> str | None:
    deal = db.query(CrmDeal).filter(CrmDeal.rd_id == deal_rd_id).one_or_none()
    return deal.current_owner_rd_id if deal else None


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

    if nome == "marcar_reuniao":
        resumo = entrada.get("resumo", "")
        if modo_teste or not deal_rd_id:
            return (
                "[MODO TESTE -- nada foi alterado no CRM] Em produção, a negociação "
                "seria movida pra etapa 'Reunião Agendada' e uma tarefa de follow-up "
                f"seria criada. Resumo combinado: {resumo}"
            )
        mover_negociacao_para_etapa(db, deal_rd_id, settings.meta_capi_trigger_stage_rd_id)
        owner = _dono_da_negociacao(db, deal_rd_id)
        if owner:
            criar_tarefa(
                db,
                deal_rd_id,
                tipo="call",
                texto=f"Confirmar reunião agendada pelo agente {NOME_AGENTE}: {resumo}",
                responsavel_rd_id=owner,
                criado_por_rd_id=owner,
                prazo=datetime.now(timezone.utc) + timedelta(hours=2),
            )
        return "Reunião registrada no CRM com sucesso."

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
                responsavel_rd_id=owner,
                criado_por_rd_id=owner,
                prazo=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        return "Encaminhado pra um humano assumir esse ponto."

    return f"Ferramenta desconhecida: {nome}"


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
            system=SYSTEM_PROMPT,
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

    texto_final = "\n\n".join(p.strip() for p in partes_texto if p.strip())
    return texto_final, mensagens
