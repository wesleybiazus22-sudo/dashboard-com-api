"""Helpers de parsing e upsert para os relatorios do GA4.

Ao contrario do RD (chave `rd_id`) e do Meta (chave `meta_id` ou composta fixa
`ad_meta_id`+`date`), aqui toda linha de todo relatorio e uma agregacao SEM id
proprio -- a chave natural muda de relatorio pra relatorio (so `date` no resumo
diario; `date`+pais+cidade na geografia; etc). Por isso o upsert e genérico,
recebendo o dict de campos-chave em vez de nomes fixos de coluna.
"""

from datetime import date, datetime
from urllib.parse import unquote_plus

from sqlalchemy.orm import Session


def upsert_by_composite(db: Session, model, chave: dict, values: dict):
    """Busca uma linha existente pela combinacao exata de `chave` (dict de
    campo->valor) e faz upsert. Idempotente: rodar a sincronizacao de novo pros
    mesmos dias/dimensoes so atualiza os valores."""
    consulta = db.query(model)
    for campo, valor in chave.items():
        consulta = consulta.filter(getattr(model, campo) == valor)
    obj = consulta.one_or_none()
    if obj is None:
        obj = model(**chave)
        db.add(obj)
    for campo, valor in values.items():
        setattr(obj, campo, valor)
    if hasattr(obj, "synced_at"):
        obj.synced_at = datetime.utcnow()
    db.flush()
    return obj


def parse_ga4_date(value) -> date | None:
    """A dimensao `date` do GA4 vem no formato YYYYMMDD (ex: "20260825"), sem
    separadores -- diferente do RD/Meta, que usam ISO-8601 com hifen."""
    if not value or len(str(value)) != 8:
        return None
    try:
        return datetime.strptime(str(value), "%Y%m%d").date()
    except ValueError:
        return None


def parse_int(value) -> int | None:
    """Metricas de contagem do GA4 vem como STRING (ex: "42"), nao numero."""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def clean_utm(value) -> str:
    """Normaliza um valor de UTM vindo do GA4.

    Algumas ferramentas de anuncio montam o link com espaco literalmente codificado
    como "+" (convencao de query string tipo formulario) em vez de "%20" -- o GA4 nao
    decodifica isso sozinho, entao a MESMA campanha aparece duas vezes no relatorio:
    uma como "Campanha X" e outra como "Campanha+X". `unquote_plus` resolve os dois
    casos de uma vez (decodifica "+" em espaco E qualquer sequencia "%XX" que tenha
    sobrado), o que junta essas variantes na mesma linha -- essencial pra nao
    subestimar o total de uma campanha so por causa de como o link foi montado."""
    if not value:
        return "(not set)"
    try:
        return unquote_plus(str(value))
    except Exception:
        return str(value)


def parse_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
