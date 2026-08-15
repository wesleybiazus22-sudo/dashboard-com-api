from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from database.connection import get_db
from ingestion.rd_crm.auth import exchange_code_for_token, get_authorization_url

router = APIRouter(prefix="/auth/rd", tags=["auth"])


@router.get("/login")
def login():
    """Abra esta rota no navegador para iniciar a autorizacao do app no RD Station CRM."""
    return RedirectResponse(get_authorization_url())


@router.get("/callback")
def callback(code: str = Query(...), db: Session = Depends(get_db)):
    """URL de callback cadastrada no app do RD Station. Troca o `code` por tokens e salva no banco."""
    try:
        exchange_code_for_token(db, code)
    except Exception as exc:  # noqa: BLE001 - queremos reportar qualquer falha de troca de token
        raise HTTPException(status_code=400, detail=f"Falha ao trocar code por token: {exc}") from exc

    return {
        "status": "success",
        "message": "RD Station CRM autorizado com sucesso. Voce ja pode rodar a sincronizacao inicial "
        "(POST /sync/rd/full) ou o script `python -m ingestion.sync_all full`.",
    }
