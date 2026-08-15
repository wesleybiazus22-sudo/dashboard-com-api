from sqlalchemy.orm import Session

from database.models import CrmLostReason
from ingestion.rd_crm.client import RDCrmClient
from ingestion.rd_crm.entities import upsert_by_rd_id

ENDPOINT = "/deal_lost_reasons"


def sync_lost_reasons(db: Session) -> int:
    client = RDCrmClient(db)
    count = 0
    for item in client.paginate(ENDPOINT):
        upsert_by_rd_id(
            db,
            CrmLostReason,
            item["id"],
            {"name": item.get("name"), "raw": item},
        )
        count += 1
    db.commit()
    return count
