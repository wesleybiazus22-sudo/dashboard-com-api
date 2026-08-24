"""Sincroniza as tabelas de lookup de origem e campanha do RD CRM.

A negociacao no RD carrega apenas `source_id`/`campaign_id` -- ids opacos. Sem
estas duas tabelas o dashboard nao consegue nem exibir nem filtrar por origem,
que e a dimensao que separa "Melhor Venda" (outbound proprio) de "Feiras e
Eventos" e "Prospeccao Ativa" -- canais com economia completamente diferente.

Ambos os endpoints sao pequenos (dezenas de linhas) e mudam raramente, entao
rodam junto do sync `full` e tambem no `incremental` (custo desprezivel).
"""

from sqlalchemy.orm import Session

from database.models import CrmCampaign, CrmDealSource
from ingestion.rd_crm.client import RDCrmClient
from ingestion.rd_crm.entities import upsert_by_rd_id

SOURCES_ENDPOINT = "/sources"
CAMPAIGNS_ENDPOINT = "/campaigns"


def sync_deal_sources(db: Session) -> int:
    client = RDCrmClient(db)
    count = 0
    for item in client.paginate(SOURCES_ENDPOINT):
        upsert_by_rd_id(db, CrmDealSource, item["id"], {"name": item.get("name"), "raw": item})
        count += 1
    db.commit()
    return count


def sync_campaigns(db: Session) -> int:
    client = RDCrmClient(db)
    count = 0
    for item in client.paginate(CAMPAIGNS_ENDPOINT):
        upsert_by_rd_id(db, CrmCampaign, item["id"], {"name": item.get("name"), "raw": item})
        count += 1
    db.commit()
    return count
