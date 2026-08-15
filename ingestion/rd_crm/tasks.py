from sqlalchemy.orm import Session

from database.models import CrmTask
from ingestion.rd_crm.client import RDCrmClient
from ingestion.rd_crm.entities import parse_dt, upsert_by_rd_id

ENDPOINT = "/tasks"


def sync_tasks(db: Session, updated_since: str | None = None) -> int:
    client = RDCrmClient(db)
    params = {"filter": f"updated_at:>{updated_since}"} if updated_since else None

    count = 0
    for item in client.paginate(ENDPOINT, params=params):
        # Padrao confirmado em deals/contacts: campos de referencia vem diretos
        # (deal_id, user_id), nao aninhados. Mantemos fallback aninhado por seguranca
        # ate confirmar com `python -m scripts.dump_sample tasks`.
        deal = item.get("deal") or {}
        owner = item.get("user") or item.get("owner") or {}

        upsert_by_rd_id(
            db,
            CrmTask,
            item["id"],
            {
                "deal_rd_id": item.get("deal_id") or deal.get("id"),
                "type": item.get("type"),
                "subject": item.get("subject") or item.get("text"),
                "owner_rd_id": item.get("user_id") or item.get("owner_id") or owner.get("id"),
                "status": item.get("status") or ("done" if item.get("done") else "pending"),
                "due_at": parse_dt(item.get("due_date") or item.get("date")),
                "completed_at": parse_dt(item.get("finished_at") or item.get("done_at")),
                "raw": item,
            },
        )
        count += 1
    db.commit()
    return count
