from sqlalchemy.orm import Session

from database.models import CrmOrganization
from ingestion.rd_crm.client import RDCrmClient
from ingestion.rd_crm.entities import upsert_by_rd_id

ENDPOINT = "/organizations"


def sync_organizations(db: Session, updated_since: str | None = None) -> int:
    client = RDCrmClient(db)
    params = {"updated_at": updated_since} if updated_since else None

    count = 0
    for item in client.paginate(ENDPOINT, params=params):
        upsert_by_rd_id(
            db,
            CrmOrganization,
            item["id"],
            {
                "name": item.get("name"),
                "segment": (item.get("segment") or {}).get("name") if isinstance(item.get("segment"), dict) else item.get("segment"),
                "raw": item,
            },
        )
        count += 1
    db.commit()
    return count
