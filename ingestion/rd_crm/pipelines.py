from sqlalchemy.orm import Session

from database.models import CrmPipeline, CrmStage
from ingestion.rd_crm.client import RDCrmClient
from ingestion.rd_crm.entities import upsert_by_rd_id

# Endpoint conforme docs atuais: GET /deal_pipelines (cada pipeline traz "deal_stages" aninhado)
ENDPOINT = "/deal_pipelines"


def sync_pipelines_and_stages(db: Session) -> tuple[int, int]:
    client = RDCrmClient(db)
    pipelines_count = 0
    stages_count = 0

    for pipeline in client.paginate(ENDPOINT):
        upsert_by_rd_id(
            db,
            CrmPipeline,
            pipeline["id"],
            {"name": pipeline.get("name"), "raw": pipeline},
        )
        pipelines_count += 1

        for stage in pipeline.get("deal_stages", []):
            upsert_by_rd_id(
                db,
                CrmStage,
                stage["id"],
                {
                    "pipeline_rd_id": pipeline["id"],
                    "name": stage.get("name"),
                    "order": stage.get("order") or stage.get("nickname_order"),
                    "raw": stage,
                },
            )
            stages_count += 1

    db.commit()
    return pipelines_count, stages_count
