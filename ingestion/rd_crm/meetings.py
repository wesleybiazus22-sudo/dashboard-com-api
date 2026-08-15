from sqlalchemy.orm import Session

from database.models import CrmMeeting
from ingestion.rd_crm.client import RDCrmClient
from ingestion.rd_crm.entities import parse_dt, upsert_by_rd_id

# Endpoint confirmado via developers.rdstation.com (crm-v2-update-meeting referencia
# GET/PATCH /crm/v2/meetings/{id}).
ENDPOINT = "/meetings"


def sync_meetings(db: Session, updated_since: str | None = None) -> int:
    client = RDCrmClient(db)
    params = {"filter": f"updated_at:>{updated_since}"} if updated_since else None

    count = 0
    for item in client.paginate(ENDPOINT, params=params):
        # Fallback aninhado por seguranca ate confirmar com
        # `python -m scripts.dump_sample meetings` (endpoint nao aceita page_size=1
        # se a conta nao tiver reunioes cadastradas -- nesse caso a lista vem vazia).
        deal = item.get("deal") or {}
        owner = item.get("user") or item.get("owner") or {}

        upsert_by_rd_id(
            db,
            CrmMeeting,
            item["id"],
            {
                "deal_rd_id": item.get("deal_id") or deal.get("id"),
                "owner_rd_id": item.get("user_id") or item.get("owner_id") or owner.get("id"),
                "status": item.get("status"),  # scheduled / completed / no_show
                "scheduled_at": parse_dt(item.get("date") or item.get("scheduled_at")),
                "raw": item,
            },
        )
        count += 1
    db.commit()
    return count
