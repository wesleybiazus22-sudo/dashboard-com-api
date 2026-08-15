"""
Orquestrador de sincronizacao do RD CRM.

Uso:
    python -m ingestion.sync_all full          # carga inicial completa (rode uma vez)
    python -m ingestion.sync_all incremental   # so o que mudou (rode a cada 5-15 min via agendador)
"""

import sys
from datetime import datetime, timezone

from database.connection import session_scope
from database.models import SyncState
from ingestion.rd_crm import contacts as contacts_sync
from ingestion.rd_crm import deals as deals_sync
from ingestion.rd_crm import lost_reasons as lost_reasons_sync
from ingestion.rd_crm import meetings as meetings_sync
from ingestion.rd_crm import organizations as organizations_sync
from ingestion.rd_crm import pipelines as pipelines_sync
from ingestion.rd_crm import tasks as tasks_sync
from ingestion.rd_crm import users as users_sync

INCREMENTAL_ENTITIES = ["organizations", "contacts", "deals", "tasks", "meetings"]


def _get_last_synced_at(db, entity: str):
    state = db.query(SyncState).filter(SyncState.entity_name == entity).one_or_none()
    return state.last_synced_at if state else None


def _set_last_synced_at(db, entity: str, when) -> None:
    state = db.query(SyncState).filter(SyncState.entity_name == entity).one_or_none()
    if state is None:
        state = SyncState(entity_name=entity)
        db.add(state)
    state.last_synced_at = when
    db.commit()


def run_full_sync() -> None:
    with session_scope() as db:
        print("Sincronizando usuarios...")
        print(f"  {users_sync.sync_users(db)} usuarios")

        print("Sincronizando pipelines e etapas...")
        p, s = pipelines_sync.sync_pipelines_and_stages(db)
        print(f"  {p} pipelines, {s} etapas")

        print("Sincronizando motivos de perda...")
        print(f"  {lost_reasons_sync.sync_lost_reasons(db)} motivos de perda")

        print("Sincronizando empresas...")
        print(f"  {organizations_sync.sync_organizations(db)} empresas")

        print("Sincronizando contatos...")
        print(f"  {contacts_sync.sync_contacts(db)} contatos")

        print("Sincronizando negociacoes (isso pode demorar)...")
        print(f"  {deals_sync.sync_deals_full(db)} negociacoes")

        print("Sincronizando tarefas...")
        print(f"  {tasks_sync.sync_tasks(db)} tarefas")

        print("Sincronizando reunioes...")
        print(f"  {meetings_sync.sync_meetings(db)} reunioes")

    now = datetime.now(timezone.utc)
    with session_scope() as db:
        for entity in INCREMENTAL_ENTITIES:
            _set_last_synced_at(db, entity, now)

    print("Carga inicial concluida.")


def run_incremental_sync() -> None:
    now = datetime.now(timezone.utc)

    with session_scope() as db:
        # Cadastros pequenos: sempre re-sincroniza por completo, e nao por delta.
        users_sync.sync_users(db)
        pipelines_sync.sync_pipelines_and_stages(db)
        lost_reasons_sync.sync_lost_reasons(db)

        since = _get_last_synced_at(db, "organizations")
        n = organizations_sync.sync_organizations(db, updated_since=since.isoformat() if since else None)
        print(f"  {n} empresas atualizadas")

        since = _get_last_synced_at(db, "contacts")
        n = contacts_sync.sync_contacts(db, updated_since=since.isoformat() if since else None)
        print(f"  {n} contatos atualizados")

        since = _get_last_synced_at(db, "deals")
        if since is None:
            n = deals_sync.sync_deals_full(db)
        else:
            n = deals_sync.sync_deals_incremental(db, updated_since_iso=since.isoformat())
        print(f"  {n} negociacoes atualizadas")

        since = _get_last_synced_at(db, "tasks")
        n = tasks_sync.sync_tasks(db, updated_since=since.isoformat() if since else None)
        print(f"  {n} tarefas atualizadas")

        since = _get_last_synced_at(db, "meetings")
        n = meetings_sync.sync_meetings(db, updated_since=since.isoformat() if since else None)
        print(f"  {n} reunioes atualizadas")

    with session_scope() as db:
        for entity in INCREMENTAL_ENTITIES:
            _set_last_synced_at(db, entity, now)

    print("Sincronizacao incremental concluida.")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "incremental"
    if mode == "full":
        run_full_sync()
    elif mode == "incremental":
        run_incremental_sync()
    else:
        print("Uso: python -m ingestion.sync_all [full|incremental]")
        sys.exit(1)
