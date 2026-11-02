"""Endpoints da captacao presencial (ver whatsapp/evento.py).

Montado como SUB-APLICACAO em /eventos (ver api/main.py), nao como router comum, de
proposito: a apresentacao roda em `localhost`, `file://` ou numa URL publica, entao
o navegador exige CORS aberto -- e abrir CORS no app inteiro tambem o abriria nos
webhooks e no disparo de sync. No sub-app ele vale so aqui.

CORS aberto e seguro NESTE caso porque nao ha cookie nem credencial: nada que o
navegador mande "automaticamente" autoriza coisa alguma. A protecao real e outra --
janela de datas do evento, lista fechada de responsaveis, consentimento obrigatorio,
teto de leads por hora e idempotencia por `client_id` (ver `registrar_lead`).
"""

import logging
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config.settings import settings
from database.connection import get_db
from database.models import EventoLead
from whatsapp import evento

logger = logging.getLogger(__name__)

eventos_app = FastAPI(title="Eventos", docs_url=None, redoc_url=None, openapi_url=None)
eventos_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type"],
    allow_credentials=False,
    max_age=86400,
)


@eventos_app.exception_handler(evento.ErroDoLead)
async def _erro_do_lead(_request, exc: evento.ErroDoLead):
    return JSONResponse(status_code=exc.status_http, content={"detail": str(exc)})


class Etapa2(BaseModel):
    provedor: str | None = Field(default=None, max_length=200)
    cidade: str | None = Field(default=None, max_length=120)
    uf: str | None = Field(default=None, max_length=2)
    assinantes: int | None = Field(default=None, ge=0, le=10_000_000)
    erp: str | None = Field(default=None, max_length=120)
    observacoes: str | None = Field(default=None, max_length=2000)


class LeadEntrada(Etapa2):
    client_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    vendedor: str = Field(min_length=1, max_length=60)
    nome: str = Field(min_length=1, max_length=120)
    celular: str = Field(min_length=8, max_length=30)
    email: str | None = Field(default=None, max_length=200)
    consentimento: bool
    capturado_em: datetime | None = None


def _resposta(lead: EventoLead) -> dict:
    """O que a pagina precisa saber. `erro` so vai quando o envio nao deu certo."""
    return {
        "client_id": lead.client_id,
        "status": lead.status,
        "card": bool(lead.deal_rd_id),
        "erro": lead.erro if lead.status in (evento.STATUS_FALHOU, evento.STATUS_INCERTO, evento.STATUS_DUPLICADO) else None,
    }


def _buscar(db: Session, client_id: str) -> EventoLead:
    lead = db.query(EventoLead).filter(EventoLead.client_id == client_id).one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead nao encontrado.")
    return lead


@eventos_app.get("/config")
def configuracao():
    """A pagina le a lista de responsaveis daqui (e guarda em cache local pro modo
    offline) -- trocar quem aparece e so mudar EVENTO_VENDEDORES no servidor, sem
    editar nem reenviar o HTML."""
    return {
        "evento": settings.evento_nome,
        "vendedores": evento.vendedores(),
        "aberto": evento.janela_aberta(),
    }


@eventos_app.post("/leads")
def criar_lead(dados: LeadEntrada, db: Session = Depends(get_db)):
    """Etapa 1: registra o lead e aciona o agente. Idempotente por `client_id` --
    a pagina pode (e vai) chamar de novo ate receber uma resposta."""
    lead = evento.registrar_lead(
        db,
        client_id=dados.client_id, vendedor=dados.vendedor, nome=dados.nome, celular=dados.celular,
        email=dados.email, consentimento=dados.consentimento, capturado_em=dados.capturado_em,
        extras=dados.model_dump(include=set(Etapa2.model_fields)),
    )
    try:
        evento.acionar(db, lead)
    except Exception as exc:  # noqa: BLE001 -- ex: RD fora do ar. O lead JA esta salvo; a pagina tenta de novo.
        db.rollback()
        logger.exception("Evento: falha ao acionar o lead %s.", dados.client_id)
        lead = _buscar(db, dados.client_id)
        lead.erro = f"{type(exc).__name__}: {str(exc)[:300]}"
        db.commit()
    return _resposta(lead)


@eventos_app.patch("/leads/{client_id}")
def atualizar_etapa2(client_id: str, dados: Etapa2, db: Session = Depends(get_db)):
    """Etapa 2: o resto do cadastro. Se o card ja existe, leva ao RD o que ele aceita."""
    lead = _buscar(db, client_id)
    evento.atualizar_etapa2(db, lead, dados.model_dump())
    return _resposta(_buscar(db, client_id))


@eventos_app.post("/leads/{client_id}/reenviar")
def reenviar(client_id: str, db: Session = Depends(get_db)):
    """Reenvio explicito, depois que o vendedor confirmar que a mensagem NAO chegou
    (status `incerto`: o envio deu timeout e pode ter sido entregue)."""
    lead = _buscar(db, client_id)
    evento.reenviar(db, lead)
    return _resposta(_buscar(db, client_id))
