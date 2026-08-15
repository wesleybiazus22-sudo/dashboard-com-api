"""Helpers compartilhados por todos os módulos de ingestão de entidades do RD CRM."""

from datetime import datetime

from sqlalchemy.orm import Session


def upsert_by_rd_id(db: Session, model, rd_id: str, values: dict):
    """Insere ou atualiza uma linha pela chave natural rd_id (idempotente).

    A sessao usa autoflush=False, entao sem o flush() explicito abaixo um rd_id que
    aparece duas vezes no mesmo lote (ex: o mesmo registro caindo em duas paginas
    porque a base do RD estava sendo atualizada durante a sincronizacao) nao seria
    encontrado na segunda vez -- gerando um INSERT duplicado e violando a constraint
    unique de rd_id. O flush torna a linha recem-adicionada visivel para a proxima
    consulta dentro da mesma transacao, sem precisar commitar.
    """
    obj = db.query(model).filter(model.rd_id == rd_id).one_or_none()
    if obj is None:
        obj = model(rd_id=rd_id)
        db.add(obj)
    for key, value in values.items():
        setattr(obj, key, value)
    if hasattr(obj, "synced_at"):
        obj.synced_at = datetime.utcnow()
    db.flush()
    return obj


def parse_dt(value) -> datetime | None:
    """Converte datas ISO-8601 do RD (com ou sem 'Z') em datetime. Retorna None se vazio/invalido."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
