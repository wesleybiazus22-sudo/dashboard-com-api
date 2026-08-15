from sqlalchemy.orm import Session

from database.models import CrmMeeting
from ingestion.rd_crm.client import RDCrmClient
from ingestion.rd_crm.entities import parse_dt, upsert_by_rd_id

# ATENÇÃO: confirme este endpoint contra a documentação atual do RD CRM v2 antes do
# primeiro sync -- reuniões podem estar em /meetings ou como um "type" dentro de /tasks
# (nesse caso, filtre sync_tasks por type == 'meeting' em vez de usar este módulo).
ENDPOINT = "/meetings"


def sync_meetings(db: Session, updated_since: str | None = None) -> int:
    client = RDCrmClient(db)
    params = {"updated_at": updated_since} if updated_since else None

    count = 0
    for item in client.paginate(ENDPOINT, params=params):
        deal = item.get("deal") or {}
        owner = item.get("user") or item.get("owner") or {}

        upsert_by_rd_id(
            db,
            CrmMeeting,
            item["id"],
            {
                "deal_rd_id": deal.get("id"),
                "owner_rd_id": owner.get("id"),
                "status": item.get("status"),  # scheduled / completed / no_show
                "scheduled_at": parse_dt(item.get("date") or item.get("scheduled_at")),
                "raw": item,
            },
        )
        count += 1
    db.commit()
    return count
