"""
Forca uma carga completa (sem filtro de data) de todas as empresas -- util depois de
adicionar um campo novo ao model (ex: legal_name) que precisa ser retroativamente
preenchido pra registros que ja existiam e nao foram tocados desde entao.

Uso: python -m scripts.resync_organizations
"""

from database.connection import session_scope
from ingestion.rd_crm import organizations as organizations_sync

if __name__ == "__main__":
    with session_scope() as db:
        n = organizations_sync.sync_organizations(db)
    print(f"{n} empresas resincronizadas.")
