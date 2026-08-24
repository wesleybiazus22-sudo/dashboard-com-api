from sqlalchemy.orm import Session

from database.models import CrmTask
from ingestion.rd_crm.client import RDCrmClient
from ingestion.rd_crm.entities import parse_dt, upsert_by_rd_id

ENDPOINT = "/tasks"


def sync_tasks(db: Session, updated_since: str | None = None) -> int:
    client = RDCrmClient(db)
    # O endpoint /tasks devolve 500 com filter=updated_at:>... (mesma sintaxe que
    # funciona em /deals, /organizations, /contacts). Ate confirmar a causa, ignoramos
    # `updated_since` e buscamos a lista completa -- aceitavel pro volume de tarefas.
    params = None

    count = 0
    for item in client.paginate(ENDPOINT, params=params):
        # Nomes reais confirmados contra o payload do RD v2 (ver raw): o assunto vem
        # em `name` (nao `subject`/`text`), a conclusao em `completed_at` (nao
        # `finished_at`/`done_at`) e o responsavel em `owner_ids` -- uma LISTA, nao um
        # `user_id` escalar. Os fallbacks antigos nunca casavam com nada, e o efeito
        # era silencioso: as colunas ficavam nulas em 100% das linhas e toda a dimensao
        # de atividade (canal de toque, tempo ate o primeiro contato) ficava morta.
        deal = item.get("deal") or {}
        owner_ids = item.get("owner_ids") or []

        upsert_by_rd_id(
            db,
            CrmTask,
            item["id"],
            {
                "deal_rd_id": item.get("deal_id") or deal.get("id"),
                "type": item.get("type"),
                "subject": item.get("name") or item.get("subject") or item.get("text"),
                "owner_rd_id": (owner_ids[0] if owner_ids else None) or item.get("user_id"),
                "status": item.get("status") or ("done" if item.get("done") else "pending"),
                "due_at": parse_dt(item.get("due_date") or item.get("date")),
                "completed_at": parse_dt(item.get("completed_at")),
                "raw": item,
            },
        )
        count += 1
    db.commit()
    return count
