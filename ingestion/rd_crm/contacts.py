from sqlalchemy.orm import Session

from database.models import CrmContact
from ingestion.rd_crm.client import RDCrmClient
from ingestion.rd_crm.entities import upsert_by_rd_id

ENDPOINT = "/contacts"


def sync_contacts(db: Session, updated_since: str | None = None) -> int:
    client = RDCrmClient(db)
    params = {"filter": f"updated_at:>{updated_since}"} if updated_since else None

    count = 0
    for item in client.paginate(ENDPOINT, params=params):
        emails = item.get("emails") or []
        phones = item.get("phones") or []

        upsert_by_rd_id(
            db,
            CrmContact,
            item["id"],
            {
                "name": item.get("name"),
                "email": (emails[0].get("email") if emails else item.get("email")),
                "phone": (phones[0].get("phone") if phones else item.get("phone")),
                "organization_rd_id": item.get("organization_id"),
                "raw": item,
            },
        )
        count += 1
    db.commit()
    return count
