"""
Acoes de ESCRITA no RD CRM -- mover negociacao de etapa e criar tarefa. Ao
contrario do resto de ingestion/rd_crm/ (que so LE dados pra alimentar o
dashboard), este modulo é a base pro agente de atendimento: e o jeito dele
"agir" no CRM de verdade (avancar o funil, registrar follow-up), nao so
conversar.

Formato do payload confirmado empiricamente contra a API v2 real (nao esta
documentado de forma obvia) -- criei uma negociacao e uma tarefa de teste,
validei os dois fluxos, e arquivei a negociacao de teste como "lost" (a API
nao tem DELETE pra /deals nem /tasks, so a interface do RD permite apagar de
verdade). Pontos que a documentacao publica nao deixa claro:
- Toda escrita usa envelope estilo JSON:API: {"data": {...}}.
- Criar negociacao exige "owner_id" (singular) -- nao "user_id".
- Criar tarefa exige DOIS campos de responsavel diferentes: "owner_ids"
  (lista -- quem fica encarregado da tarefa) E "created_by_id" (quem criou) --
  faltando qualquer um dos dois, a API recusa com 422.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from ingestion.rd_crm.client import RDCrmClient

# Tipos de tarefa aceitos pela API (confirmado pelos valores ja usados na base
# sincronizada -- ver `crm_tasks.type`). Usar um fora dessa lista nao da erro
# na hora, mas tambem nao aparece corretamente na UI do RD.
TIPOS_TAREFA_VALIDOS = {"email", "call", "meeting", "whatsapp", "visit", "task"}


def mover_negociacao_para_etapa(db: Session, deal_rd_id: str, stage_rd_id: str) -> dict:
    """Move uma negociacao pra outra etapa DENTRO DO MESMO PIPELINE em que ela ja
    esta (a API nao pede pipeline_id pra so mudar de etapa). Devolve o objeto
    da negociacao atualizado, como a API devolve.

    Nao faz nenhuma validacao de "essa transicao faz sentido" -- quem chama
    (o agente, ou codigo que decide QUANDO mover) e responsavel por isso.
    """
    client = RDCrmClient(db)
    resposta = client.put(f"/deals/{deal_rd_id}", json={"data": {"stage_id": stage_rd_id}})
    return resposta["data"]


def criar_tarefa(
    db: Session,
    deal_rd_id: str,
    *,
    tipo: str,
    texto: str,
    responsavel_rd_id: str,
    criado_por_rd_id: str,
    prazo: datetime,
) -> dict:
    """Cria uma tarefa vinculada a uma negociacao (ex: "ligar pra confirmar
    reuniao", "enviar proposta"). `prazo` precisa ser timezone-aware -- a API
    espera ISO-8601 com offset (ex: "2026-09-10T15:00:00-03:00"); um datetime
    "naive" seria interpretado errado.

    `responsavel_rd_id` e `criado_por_rd_id` podem ser a mesma pessoa (o caso
    comum quando quem cria a tarefa e quem vai executa-la) ou diferentes (ex:
    o agente cria em nome de um SDR especifico)."""
    if tipo not in TIPOS_TAREFA_VALIDOS:
        raise ValueError(f"Tipo de tarefa invalido: {tipo!r}. Use um de {sorted(TIPOS_TAREFA_VALIDOS)}.")
    if prazo.tzinfo is None:
        raise ValueError("`prazo` precisa ser timezone-aware (ex: datetime com tzinfo=timezone.utc ou zoneinfo).")

    client = RDCrmClient(db)
    corpo = {
        "deal_id": deal_rd_id,
        "type": tipo,
        "name": texto,
        "due_date": prazo.isoformat(),
        "owner_ids": [responsavel_rd_id],
        "created_by_id": criado_por_rd_id,
    }
    resposta = client.post("/tasks", json={"data": corpo})
    return resposta["data"]


def atualizar_prazo_tarefa(db: Session, task_rd_id: str, *, prazo: datetime) -> dict:
    """Atualiza o `due_date` de uma tarefa ja existente -- usado pra reagendar
    a reuniao (`reagendar_reuniao` em ingestion/llm/agent.py) sem criar uma
    tarefa duplicada. VALIDADO contra a API real em 2026-09-14: lead pediu
    reagendamento numa conversa de verdade, o agente chamou essa funcao, e o
    `due_date` da tarefa foi conferido direto na API do RD (GET /tasks/{id})
    mostrando o novo horario certinho."""
    if prazo.tzinfo is None:
        raise ValueError("`prazo` precisa ser timezone-aware (ex: datetime com tzinfo=timezone.utc ou zoneinfo).")
    client = RDCrmClient(db)
    resposta = client.put(f"/tasks/{task_rd_id}", json={"data": {"due_date": prazo.isoformat()}})
    return resposta["data"]


def arquivar_negociacao_perdida(db: Session, deal_rd_id: str, *, motivo_rd_id: str, novo_nome: str | None = None) -> dict:
    """Marca uma negociacao como perdida (status='lost'). Usado pra "limpar"
    negociacoes de teste (a API do RD nao tem DELETE pra /deals) e, no uso
    real do agente, pra registrar perda automatica em casos claros (ex: lead
    pediu explicitamente pra nao ser mais contatado) -- NUNCA como decisao
    autonoma do agente em casos ambiguos, isso e julgamento humano."""
    client = RDCrmClient(db)
    corpo: dict = {"status": "lost", "lost_reason_id": motivo_rd_id}
    if novo_nome:
        corpo["name"] = novo_nome
    resposta = client.put(f"/deals/{deal_rd_id}", json={"data": corpo})
    return resposta["data"]
