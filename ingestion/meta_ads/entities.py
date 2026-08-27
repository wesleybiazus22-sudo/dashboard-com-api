"""Helpers de upsert para as entidades do Meta Ads.

Duas formas de chave natural aqui, ao contrario do RD (sempre `rd_id` simples):
- Campanha/conjunto/anuncio: chave simples (`meta_id`) -- mesmo padrao do
  `upsert_by_rd_id` de ingestion/rd_crm/entities.py, so com outro nome de coluna.
- Insight diario: chave COMPOSTA (`ad_meta_id` + `date`), porque a linha nao tem
  id proprio no Meta -- e uma metrica agregada por dia, nao uma entidade.
"""

from datetime import date, datetime

from sqlalchemy.orm import Session


def upsert_by_meta_id(db: Session, model, meta_id: str, values: dict):
    """Igual a `upsert_by_rd_id` do RD, filtrando por `meta_id` em vez de `rd_id`."""
    obj = db.query(model).filter(model.meta_id == meta_id).one_or_none()
    if obj is None:
        obj = model(meta_id=meta_id)
        db.add(obj)
    for key, value in values.items():
        setattr(obj, key, value)
    if hasattr(obj, "synced_at"):
        obj.synced_at = datetime.utcnow()
    db.flush()
    return obj


def upsert_insight_row(db: Session, model, ad_meta_id: str, dia: date, values: dict):
    """Upsert por chave composta (ad_meta_id, date). Idempotente: rodar a
    sincronizacao incremental de novo pros mesmos dias so atualiza os valores
    (importante porque re-sincronizamos uma janela de dias recentes a cada rodada --
    ver comentario em ingestion/meta_ads/sync.py sobre correcao de atribuicao)."""
    obj = (
        db.query(model)
        .filter(model.ad_meta_id == ad_meta_id, model.date == dia)
        .one_or_none()
    )
    if obj is None:
        obj = model(ad_meta_id=ad_meta_id, date=dia)
        db.add(obj)
    for key, value in values.items():
        setattr(obj, key, value)
    if hasattr(obj, "synced_at"):
        obj.synced_at = datetime.utcnow()
    db.flush()
    return obj


def parse_dt(value) -> datetime | None:
    """Converte datas ISO-8601 do Meta em datetime. Retorna None se vazio/invalido."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_date(value) -> date | None:
    """Converte datas no formato YYYY-MM-DD (como vem em date_start/date_stop dos
    insights) em `date`. Retorna None se vazio/invalido."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def parse_money(value) -> float | None:
    """Campos monetarios do Meta vem como STRING (ex: "12.34"), nao numero."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
