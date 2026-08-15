"""
Dispara sincronizacoes manualmente via HTTP. Uso tipico:
- POST /sync/rd/full         -> uma vez, apos autorizar o app (carga historica completa)
- POST /sync/rd/incremental  -> chamado pelo agendador a cada 5-15 min (cron/n8n)

Roda em background porque a carga completa pode demorar bastante dependendo do
volume de negociacoes.
"""

from fastapi import APIRouter, BackgroundTasks

from ingestion.sync_all import run_full_sync, run_incremental_sync

router = APIRouter(prefix="/sync/rd", tags=["sync"])


@router.post("/full")
def trigger_full_sync(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_full_sync)
    return {"status": "started", "mode": "full"}


@router.post("/incremental")
def trigger_incremental_sync(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_incremental_sync)
    return {"status": "started", "mode": "incremental"}
