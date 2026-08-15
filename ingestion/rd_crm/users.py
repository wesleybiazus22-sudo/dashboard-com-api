from sqlalchemy.orm import Session

from database.models import CrmUser
from ingestion.rd_crm.client import RDCrmClient
from ingestion.rd_crm.entities import upsert_by_rd_id

# Endpoint conforme docs atuais: GET /users
ENDPOINT = "/users"


def sync_users(db: Session) -> int:
    client = RDCrmClient(db)
    count = 0
    for item in client.paginate(ENDPOINT):
        upsert_by_rd_id(
            db,
            CrmUser,
            item["id"],
            {
                "name": item.get("name"),
                "email": item.get("email"),
                "is_active": item.get("is_active", True),
                "raw": item,
            },
        )
        count += 1
    db.commit()
    return count
