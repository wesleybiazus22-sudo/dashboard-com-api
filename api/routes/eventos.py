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
import time
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config.settings import settings
from database.connection import get_db
from database.models import EventoLead
from ingestion.rd_crm.actions import criar_negociacao_com_contato, criar_nota, criar_tarefa
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


class ThunderaLeadEntrada(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    empresa: str = Field(min_length=1, max_length=200)
    telefone: str = Field(min_length=6, max_length=40)
    email: str | None = Field(default=None, max_length=200)
    pais: str | None = Field(default=None, max_length=80)
    estado: str | None = Field(default=None, max_length=120)
    cidade: str | None = Field(default=None, max_length=120)
    observacoes: str | None = Field(default=None, max_length=2000)


# Disjuntor simples (sem tabela propria: este endpoint so cria o card, nao aciona
# agente nenhum, entao o custo de um excesso e so "polui o CRM", nao "manda
# WhatsApp indevido" -- um teto em memoria basta). Zera sozinho a cada hora;
# reinicio do processo (deploy, o servidor gratuito dormir/acordar) tambem zera,
# o que e aceitavel pra essa finalidade.
_thundera_lead_timestamps: list[float] = []
_THUNDERA_MAX_LEADS_POR_HORA = 60


@eventos_app.post("/thundera-leads")
def criar_lead_thundera(dados: ThunderaLeadEntrada, db: Session = Depends(get_db)):
    """Formulario do site Thundera IA Lite (thundera-lite-br-py, js/leads.js):
    cria organizacao + contato + negociacao no funil '[ThunderaLite] -
    Qualificacao', etapa 'Primeira conexao'. Ao contrario de /eventos/leads
    (exclusivo da Maquina.ISP), NAO aciona nenhum agente de WhatsApp -- a pagina
    continua salvando o lead localmente e exportando planilha de qualquer forma;
    esta chamada e so uma tentativa extra, best-effort, de ja deixar o card
    pronto no CRM."""
    agora = time.monotonic()
    global _thundera_lead_timestamps
    _thundera_lead_timestamps = [t for t in _thundera_lead_timestamps if agora - t < 3600]
    if len(_thundera_lead_timestamps) >= _THUNDERA_MAX_LEADS_POR_HORA:
        raise HTTPException(status_code=429, detail="Limite de leads por hora atingido.")
    _thundera_lead_timestamps.append(agora)

    nome_empresa = dados.empresa.strip() or dados.nome.strip()
    local = " / ".join(x for x in (dados.cidade, dados.estado, dados.pais) if x and x.strip())

    try:
        negociacao = criar_negociacao_com_contato(
            db,
            nome_empresa=nome_empresa,
            nome_contato=dados.nome.strip(),
            telefone=dados.telefone.strip(),
            owner_rd_id=settings.thundera_lite_owner_rd_id,
            pipeline_rd_id=settings.rd_pipeline_thundera_lite_rd_id,
            stage_rd_id=settings.rd_stage_thundera_lite_entrada_rd_id,
            source_rd_id=settings.rd_source_evento_rd_id,
            email=dados.email,
        )
    except Exception as exc:  # noqa: BLE001 -- a pagina ja salvou o lead local; so loga e devolve erro
        logger.exception("Thundera Lite: falha ao criar o card no CRM para %r.", dados.nome)
        raise HTTPException(status_code=502, detail="Falha ao registrar no CRM.") from exc

    if local:
        try:
            criar_tarefa(
                db, negociacao["id"], tipo="task",
                texto=f"Lead captado pelo formulario do site Thundera IA Lite. Local informado: {local}.",
                responsavel_rd_id=settings.thundera_lite_owner_rd_id,
                criado_por_rd_id=settings.thundera_lite_owner_rd_id,
                prazo=datetime.now(timezone.utc) + timedelta(days=1),
            )
        except Exception:  # noqa: BLE001 -- o card ja existe; a tarefa e so um complemento
            logger.exception("Thundera Lite: falha ao anotar o local do lead (negociacao %s).", negociacao["id"])

    observacoes = (dados.observacoes or "").strip()
    if observacoes:
        try:
            criar_nota(
                db, negociacao["id"],
                texto=observacoes,
                autor_rd_id=settings.thundera_lite_owner_rd_id,
            )
        except Exception:  # noqa: BLE001 -- o card ja existe; a nota e so um complemento
            logger.exception("Thundera Lite: falha ao criar a nota de observacoes (negociacao %s).", negociacao["id"])

    return {"status": "ok", "deal_id": negociacao["id"]}


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
