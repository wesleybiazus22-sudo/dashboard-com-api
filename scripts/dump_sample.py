"""
Busca 1 registro cru de cada entidade principal do RD CRM e imprime o JSON completo.

Use isto ANTES de rodar a carga completa, para confirmar os nomes de campo reais
(deal_stage, deal_pipeline, organization, contacts, user, deal_source,
deal_lost_reason...) contra o que `ingestion/rd_crm/deals.py::extract_deal_fields`
assume. Se algo estiver diferente, ajuste apenas aquela funcao.

Uso: python -m scripts.dump_sample deals
     python -m scripts.dump_sample organizations
     python -m scripts.dump_sample contacts
"""

import json
import sys

from database.connection import session_scope
from ingestion.rd_crm.client import RDCrmClient

ENDPOINTS = {
    "deals": "/deals",
    "organizations": "/organizations",
    "contacts": "/contacts",
    "users": "/users",
    "pipelines": "/pipelines",
    "lost_reasons": "/lost_reasons",
    "tasks": "/tasks",
    "meetings": "/meetings",
}

if __name__ == "__main__":
    entity = sys.argv[1] if len(sys.argv) > 1 else "deals"
    if entity not in ENDPOINTS:
        print(f"Entidade desconhecida. Opcoes: {list(ENDPOINTS)}")
        sys.exit(1)

    with session_scope() as db:
        client = RDCrmClient(db)
        for item in client.paginate(ENDPOINTS[entity], page_size=1):
            print(json.dumps(item, indent=2, ensure_ascii=False))
            break
        else:
            print("Nenhum registro retornado.")
