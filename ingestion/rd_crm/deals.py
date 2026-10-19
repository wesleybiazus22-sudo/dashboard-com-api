"""
Sincronização de negociações (deals) do RD CRM.

Formato confirmado via `python -m scripts.dump_sample deals` (payload real): os campos
sao diretos no objeto da negociacao -- pipeline_id, stage_id, owner_id, organization_id,
lost_reason_id, source_id, contact_ids (lista; usamos o primeiro como contato principal)
-- e nao objetos aninhados como "deal_stage"/"organization", que era a suposicao inicial
e causava 401/dados vazios. Valor monetario vem em `total_price` (nao ha campo "amount").
Campos ainda nao observados num payload real (campaign_id, expected_close_date) mantem
fallback defensivo -- reconfirme com `dump_sample` se algo mudar.
"""

from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from database.models import CrmDeal
from ingestion.rd_crm.client import RDCrmClient
from ingestion.rd_crm.deal_history import snapshot_deal, sync_deal_history
from ingestion.rd_crm.entities import parse_dt, upsert_by_rd_id

ENDPOINT = "/deals"

# Valor proprio do espelho local (nao existe no RD): negociacao que foi APAGADA la.
# Ver `marcar_excluidas_no_rd`. Quem le crm_deals pra mostrar pipeline ou agir em
# negociacao precisa ignorar esse status.
STATUS_EXCLUIDA = "deleted"


def _extract_fbclid(item: dict) -> str | None:
    """Procura o fbclid dentro de `custom_fields`, tolerando os dois formatos que a
    API do RD ja usou historicamente: dict {chave: valor} (chave pode ser o NOME do
    campo ou o id interno do RD, ainda nao confirmado contra um payload real -- ver
    docstring do modulo) ou lista de {custom_field_id/label, value}. Procura por
    "fbclid" como SUBSTRING (case-insensitive) do nome/chave, entao funciona mesmo
    se o campo personalizado for criado como "Fbclid", "fb_clid" etc.
    Retorna None sem erro se o campo nao existir ainda (a maioria das negociacoes,
    ate o campo ser configurado no RD -- ver README secao 14)."""
    custom = item.get("custom_fields")
    if not custom:
        return None

    if isinstance(custom, dict):
        for chave, valor in custom.items():
            if "fbclid" in str(chave).lower() and valor:
                return str(valor)
    elif isinstance(custom, list):
        for campo in custom:
            if not isinstance(campo, dict):
                continue
            rotulo = str(campo.get("custom_field_id") or campo.get("label") or campo.get("name") or "")
            if "fbclid" in rotulo.lower() and campo.get("value"):
                return str(campo["value"])
    return None


def _extract_utm_medium(item: dict) -> str | None:
    """UTM medium (ex: "paid_social") dentro de `custom_fields` -- muito mais
    confiavel pra identificar trafego pago que o campo "Fonte" (source_id, ver
    WHATSAPP_AGENT_TRIGGER_SOURCE_RD_IDS em config/settings.py): confirmado em
    2026-09-19 que uma negociacao real de trafego pago (fbclid + utm_source=
    Instagram_Reels no custom_fields) chegou com source_id = "Desconhecido",
    fazendo o gatilho de primeiro contato (na epoca, baseado so em source)
    pular o lead silenciosamente. Ao contrario de `_extract_fbclid`, casa a
    CHAVE EXATA "utm_medium" (case-insensitive) -- nao substring -- pra nao
    confundir com "utm_medium_secundario" ou similar caso apareca."""
    custom = item.get("custom_fields")
    if not custom:
        return None

    if isinstance(custom, dict):
        for chave, valor in custom.items():
            if str(chave).strip().lower() == "utm_medium" and valor:
                return str(valor).strip()
    elif isinstance(custom, list):
        for campo in custom:
            if not isinstance(campo, dict):
                continue
            rotulo = str(campo.get("custom_field_id") or campo.get("label") or campo.get("name") or "")
            if rotulo.strip().lower() == "utm_medium" and campo.get("value"):
                return str(campo["value"]).strip()
    return None


def extract_deal_fields(item: dict) -> dict:
    return {
        "name": item.get("name"),
        "amount": item.get("amount") if item.get("amount") is not None else item.get("total_price"),
        "currency": item.get("currency") or "BRL",
        "pipeline_rd_id": item.get("pipeline_id"),
        "stage_rd_id": item.get("stage_id"),
        "status": item.get("status") or item.get("deal_status"),  # open / won / lost
        "organization_rd_id": item.get("organization_id"),
        "contact_rd_id": item.get("contact_id") or (item.get("contact_ids") or [None])[0],
        "current_owner_rd_id": item.get("owner_id"),
        "campaign": item.get("campaign_id"),
        "source": item.get("source_id"),
        "utm_medium": _extract_utm_medium(item),
        "fbclid": _extract_fbclid(item),
        "lost_reason_rd_id": item.get("lost_reason_id"),
        "deal_created_at": parse_dt(item.get("created_at")),
        "deal_updated_at": parse_dt(item.get("updated_at")),
        "closed_at": parse_dt(item.get("closed_at") or item.get("won_at") or item.get("lost_at")),
        "expected_close_date": parse_dt(item.get("expected_close_date")),
        "raw": item,
    }


def _sync(db: Session, params: dict | None) -> int:
    client = RDCrmClient(db)
    count = 0

    for item in client.paginate(ENDPOINT, params=params):
        fields = extract_deal_fields(item)
        previous = snapshot_deal(db.query(CrmDeal).filter(CrmDeal.rd_id == item["id"]).one_or_none())
        deal = upsert_by_rd_id(db, CrmDeal, item["id"], fields)
        db.flush()  # garante deal.id preenchido antes de referenciar em history
        at = fields["deal_updated_at"] or datetime.now(timezone.utc)
        sync_deal_history(db, previous, deal, fields, at)
        count += 1

    db.commit()
    return count


def sync_deals_full(db: Session) -> int:
    """Carga inicial: busca TODAS as negociações, sem filtro de data."""
    return _sync(db, params=None)


def sync_deals_incremental(db: Session, updated_since_iso: str) -> int:
    """Carga incremental: só negociações atualizadas desde a última sincronização.

    A API usa RDQL no parâmetro `filter` (não um `updated_at` solto): o operador
    "maior que" é `campo:>valor`.
    """
    return _sync(db, params={"filter": f"updated_at:>{updated_since_iso}"})


def _ids_no_rd(client: RDCrmClient, max_passadas: int = 6) -> set[str]:
    """Ids de TODAS as negociacoes do RD. Uma passada so nao basta: a listagem do RD
    nao tem ordem estavel entre paginas -- medido em 2026-10-19, com 591 negociacoes
    no RD cada passada devolvia os 591 itens, mas so 441-591 ids UNICOS (as paginas
    se sobrepoem e parte nunca aparece; nenhum parametro de sort/tamanho de pagina
    estabilizou). O que e confiavel e o TOTAL de itens, entao une passadas ate a
    uniao bater com ele -- completude verificada, nao presumida. Se nao fechar no
    limite de passadas, devolve o que tiver: o GET individual de cada candidata em
    `marcar_excluidas_no_rd` continua garantindo que nada vivo seja marcado."""
    ids: set[str] = set()
    for _ in range(max_passadas):
        total = 0
        for item in client.paginate(ENDPOINT):
            ids.add(item["id"])
            total += 1
        if len(ids) >= total:
            break
    return ids


def marcar_excluidas_no_rd(db: Session) -> int:
    """Marca como STATUS_EXCLUIDA as negociacoes que existem aqui mas foram APAGADAS
    no RD. Nem a carga incremental (so traz o que foi ATUALIZADO) nem a completa (so
    faz upsert do que existe) ficam sabendo de uma exclusao -- a negociacao apagada
    congelava no ultimo estado pra sempre. Incidente real em 2026-10-19: 17
    negociacoes apagadas no RD (testes e duplicatas) seguiam "em andamento" aqui, e
    a fotografia do funil nunca batia com o quadro do CRM.

    Lista os ids do RD (ver `_ids_no_rd`) e, pra cada candidata (existe aqui, nao
    veio na lista), CONFIRMA com um GET individual que o RD devolve 404 antes de
    marcar -- uma lista incompleta nunca pode apagar uma negociacao viva. Trava
    extra: candidatas demais num lote so indica lista quebrada, nao exclusao em
    massa; aborta sem marcar nada. Reversivel por natureza: se a negociacao voltar a
    existir no RD, a proxima sincronizacao sobrescreve o status com o valor real."""
    client = RDCrmClient(db)
    ids_no_rd = _ids_no_rd(client)
    if not ids_no_rd:
        raise RuntimeError("RD devolveu zero negociacoes -- abortado sem marcar nada.")

    candidatas = (
        db.query(CrmDeal)
        .filter(CrmDeal.status.is_distinct_from(STATUS_EXCLUIDA), CrmDeal.rd_id.notin_(ids_no_rd))
        .all()
    )
    limite = max(30, len(ids_no_rd) // 5)
    if len(candidatas) > limite:
        raise RuntimeError(
            f"{len(candidatas)} negociacoes locais nao vieram na lista do RD (limite {limite}) -- "
            "provavel lista incompleta, abortado sem marcar nada."
        )

    marcadas = 0
    for deal in candidatas:
        try:
            client.get(f"{ENDPOINT}/{deal.rd_id}")
            continue  # existe no RD, a lista e que nao trouxe -- nao mexe
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
        deal.status = STATUS_EXCLUIDA
        marcadas += 1

    db.commit()
    return marcadas
