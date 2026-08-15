from sqlalchemy.orm import Session

from database.models import CrmPipeline, CrmStage
from ingestion.rd_crm.client import RDCrmClient
from ingestion.rd_crm.entities import upsert_by_rd_id

# Confirmado via docs oficiais: GET /crm/v2/pipelines. O objeto pipeline NAO traz as
# etapas aninhadas -- so um array "stage_ids". As etapas ficam ANINHADAS SOB O PIPELINE
# (confirmado pelo endpoint de update: PATCH /pipelines/{pipeline_id}/stages/{id}) --
# nao existem soltas em /deal_stages/{id} (tentativa anterior, sempre 404). Tentamos
# primeiro a listagem completa por pipeline; se nao existir para a conta, caimos pra
# busca individual por id.
PIPELINES_ENDPOINT = "/pipelines"
STAGES_LIST_ENDPOINT = "/pipelines/{pipeline_id}/stages"
STAGE_GET_ENDPOINT = "/pipelines/{pipeline_id}/stages/{id}"


def sync_pipelines_and_stages(db: Session) -> tuple[int, int]:
    client = RDCrmClient(db)
    pipelines_count = 0
    stages_count = 0

    for pipeline in client.paginate(PIPELINES_ENDPOINT):
        upsert_by_rd_id(
            db,
            CrmPipeline,
            pipeline["id"],
            {"name": pipeline.get("name"), "raw": pipeline},
        )
        pipelines_count += 1

        stage_ids = pipeline.get("stage_ids") or []
        stages_by_id: dict[str, dict] = {}
        try:
            for stage in client.paginate(STAGES_LIST_ENDPOINT.format(pipeline_id=pipeline["id"])):
                stages_by_id[stage["id"]] = stage
        except Exception:
            pass  # cai pro fallback de busca individual abaixo

        for stage_id in stage_ids:
            stage = stages_by_id.get(stage_id)
            if stage is None:
                try:
                    stage = client.get(STAGE_GET_ENDPOINT.format(pipeline_id=pipeline["id"], id=stage_id))
                except Exception:
                    # Nenhuma das duas formas funcionou pra essa conta -- registramos
                    # so o id, sem travar o resto da sincronizacao.
                    stage = {"id": stage_id}

            upsert_by_rd_id(
                db,
                CrmStage,
                stage_id,
                {
                    "pipeline_rd_id": pipeline["id"],
                    "name": stage.get("name") or stage.get("nickname"),
                    "order": stage.get("order") or stage.get("nickname_order"),
                    "raw": stage,
                },
            )
            stages_count += 1

    db.commit()
    return pipelines_count, stages_count
