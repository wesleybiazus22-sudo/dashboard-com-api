"""
Dispara sincronizacoes manualmente via HTTP. Uso tipico:
- POST /sync/rd/full?token=...         -> uma vez, apos autorizar o app (carga historica completa)
- POST /sync/rd/incremental?token=...  -> chamado pelo agendador a cada 5-15 min (GitHub Actions)

Protegido por token simples (SYNC_TRIGGER_TOKEN) -- sem isso, qualquer um que
descobrisse a URL publica poderia disparar sincronizacoes a vontade.

Roda em background porque a carga completa pode demorar bastante dependendo do
volume de negociacoes.
"""

from secrets import compare_digest

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from config.settings import settings
from ingestion.sync_all import run_full_sync, run_incremental_sync

router = APIRouter(prefix="/sync/rd", tags=["sync"])


def _check_token(token: str) -> None:
    """Segredo vazio significa endpoint DESLIGADO, nunca "aceita qualquer token".
    Sem esta guarda, um deploy que esquecesse de definir SYNC_TRIGGER_TOKEN passaria
    a aceitar `?token=` vazio de qualquer origem -- o default vazio do Settings
    transformaria um erro de configuracao em porta aberta."""
    esperado = settings.sync_trigger_token
    if not esperado or not compare_digest(token, esperado):
        raise HTTPException(status_code=401, detail="Token invalido.")


@router.post("/full")
def trigger_full_sync(background_tasks: BackgroundTasks, token: str = Query(...)):
    _check_token(token)
    background_tasks.add_task(run_full_sync)
    return {"status": "started", "mode": "full"}


@router.post("/incremental")
def trigger_incremental_sync(background_tasks: BackgroundTasks, token: str = Query(...)):
    _check_token(token)
    background_tasks.add_task(run_incremental_sync)
    return {"status": "started", "mode": "incremental"}
