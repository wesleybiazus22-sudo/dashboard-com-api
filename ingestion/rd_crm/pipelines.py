from sqlalchemy.orm import Session

from database.models import CrmPipeline, CrmStage
from ingestion.rd_crm.client import RDCrmClient
from ingestion.rd_crm.entities import upsert_by_rd_id

# Confirmado via docs oficiais: GET /crm/v2/pipelines. O objeto pipeline NAO traz as
# etapas aninhadas -- so um array "stage_ids". Buscamos cada etapa individualmente em
# /deal_stages/{id} (nome herdado da API v1; nao ha endpoint v2 de listagem de etapas
# documentado publicamente no momento). Se esse endpoint nao existir para sua conta,
# a etapa fica sem nome/order mas o stage_id continua correto e linkado nas
# negociacoes -- valide com `python -m scripts.dump_sample pipelines` e ajuste aqui.
PIPELINES_ENDPOINT = "/pipelines"
STAGE_ENDPOINT = "/deal_stages/{id}"


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

        for stage_id in pipeline.get("stage_ids") or []:
            try:
                stage = client.get(STAGE_ENDPOINT.format(id=stage_id))
            except Exception:
                # Endpoint de etapa individual pode nao existir/ter outro path nesta
                # conta -- registramos so o id, sem travar o resto da sincronizacao.
                stage = {"id": stage_id}

            upsert_by_rd_id(
                db,
                CrmStage,
                stage_id,
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
