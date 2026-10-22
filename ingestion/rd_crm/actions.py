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

import re
import unicodedata
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from database.models import CrmContact, CrmDeal, CrmOrganization
from ingestion.rd_crm.client import RDCrmClient
from ingestion.rd_crm.entities import upsert_by_rd_id

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


def _dados(resposta: dict) -> dict:
    """Desembrulha o envelope {"data": {...}} das escritas. Algumas rotas ja
    devolvem o objeto cru -- aceita os dois formatos em vez de quebrar com
    KeyError se o RD mudar de ideia numa rota so."""
    return resposta.get("data", resposta) if isinstance(resposta, dict) else resposta


def _chave_nome_empresa(valor: str | None) -> str:
    """Chave de comparacao de nome de empresa: sem acento, so letras e numeros,
    caixa baixa. "Speed Turbo Telecom Ltda." e "SPEED TURBO TELECOM LTDA" viram
    a mesma chave -- usado pra reaproveitar a organizacao que ja existe em vez
    de criar uma duplicada pro mesmo provedor."""
    sem_acento = unicodedata.normalize("NFKD", valor or "").encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sem_acento.lower()).split())


def criar_negociacao_com_contato(
    db: Session,
    *,
    nome_empresa: str,
    nome_contato: str,
    telefone: str,
    owner_rd_id: str,
    pipeline_rd_id: str,
    stage_rd_id: str,
    source_rd_id: str,
    email: str | None = None,
    quantidade_assinantes: str | None = None,
) -> dict:
    """Cria no RD a trinca organizacao + contato + negociacao pra um lead que
    chegou SEM card nenhum -- o caso do QR code que joga o lead direto no
    WhatsApp do agente (ver `criar_negociacao_no_crm` em ingestion/llm/agent.py).
    Devolve a negociacao como a API devolve.

    Reaproveita a organizacao que ja existir com o mesmo nome (comparado por
    `_chave_nome_empresa`) em vez de criar outra: o provedor pode ja estar na
    base vindo do Melhor Venda ou de prospeccao antiga, e organizacao duplicada
    bagunca o CRM pra quem trabalha o lead depois.

    ESPELHA as tres entidades no banco local na MESMA transacao. Sem isso o card
    so apareceria aqui no proximo sync (a cada 15 min) e, ate la,
    `_deal_por_telefone` (whatsapp/processor.py) nao acharia nada -- o agente
    trataria a mensagem seguinte do lead como se ele ainda nao tivesse card e
    criaria tudo de novo, card atras de card na mesma conversa.
    """
    client = RDCrmClient(db)
    agora = datetime.now(timezone.utc)

    # --- organizacao: reaproveita a que ja existe, senao cria ---
    chave = _chave_nome_empresa(nome_empresa)
    organizacao_rd_id = None
    if chave:
        for existente in db.query(CrmOrganization).filter(CrmOrganization.name.isnot(None)).all():
            if _chave_nome_empresa(existente.name) == chave:
                organizacao_rd_id = existente.rd_id
                break

    if organizacao_rd_id is None:
        organizacao = _dados(client.post("/organizations", json={
            "data": {"name": nome_empresa, "owner_id": owner_rd_id},
        }))
        organizacao_rd_id = organizacao["id"]
        upsert_by_rd_id(db, CrmOrganization, organizacao_rd_id, {
            "name": organizacao.get("name"), "legal_name": None, "segment": None, "raw": organizacao,
        })

    # --- contato: telefone vem do proprio numero que esta conversando ---
    corpo_contato: dict = {
        "name": nome_contato,
        "phones": [{"type": "mobile", "phone": telefone}],
        "organization_id": organizacao_rd_id,
    }
    if email:
        corpo_contato["emails"] = [{"email": email}]
    contato = _dados(client.post("/contacts", json={"data": corpo_contato}))
    upsert_by_rd_id(db, CrmContact, contato["id"], {
        "name": contato.get("name"),
        "email": email,
        "phone": telefone,
        "organization_rd_id": organizacao_rd_id,
        "raw": contato,
    })

    # --- negociacao ---
    corpo_deal: dict = {
        "name": nome_empresa,
        "owner_id": owner_rd_id,
        "pipeline_id": pipeline_rd_id,
        "stage_id": stage_rd_id,
        "source_id": source_rd_id,
        "organization_id": organizacao_rd_id,
        "contact_ids": [contato["id"]],
    }
    if quantidade_assinantes:
        # Mesma chave que o RD ja usa nas negociacoes existentes (confirmado na
        # base: 43 negociacoes com "quantidade-de-clientes"), gravada como texto
        # porque o lead responde em linguagem natural ("15 mil", "3000").
        corpo_deal["custom_fields"] = {"quantidade-de-clientes": str(quantidade_assinantes)}

    negociacao = _dados(client.post("/deals", json={"data": corpo_deal}))

    # Espelho local. Import adiado de proposito: `extract_deal_fields` mora no
    # modulo de ingestao de negociacoes, que e o dono do mapeamento payload ->
    # colunas -- reaproveitar evita que esse mapeamento viva em dois lugares e
    # saia de sincronia quando um campo novo for adicionado la.
    from ingestion.rd_crm.deal_history import sync_deal_history
    from ingestion.rd_crm.deals import extract_deal_fields

    campos = extract_deal_fields(negociacao)
    deal_local = upsert_by_rd_id(db, CrmDeal, negociacao["id"], campos)
    db.flush()
    sync_deal_history(db, None, deal_local, campos, campos.get("deal_updated_at") or agora)
    db.commit()
    return negociacao
