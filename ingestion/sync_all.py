"""
Orquestrador de sincronizacao do RD CRM.

Uso:
    python -m ingestion.sync_all full          # carga inicial completa (rode uma vez)
    python -m ingestion.sync_all incremental   # so o que mudou (rode a cada 5-15 min via agendador)

Cada entidade roda isolada: se uma falhar (ex: um endpoint instavel do RD), as
outras continuam normalmente e o `sync_state` so avanca para quem realmente
teve sucesso -- na proxima rodada, a entidade que falhou tenta de novo a partir
do mesmo ponto, sem re-processar o que ja deu certo.
"""

import sys
import traceback
from datetime import datetime, timezone

from config.settings import settings
from database.connection import session_scope
from database.models import SyncState
from ingestion.meta_ads import sync as meta_sync
from ingestion.rd_crm import contacts as contacts_sync
from ingestion.rd_crm import deals as deals_sync
from ingestion.rd_crm import lost_reasons as lost_reasons_sync
from ingestion.rd_crm import meetings as meetings_sync
from ingestion.rd_crm import organizations as organizations_sync
from ingestion.rd_crm import pipelines as pipelines_sync
from ingestion.rd_crm import sources as sources_sync
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


def _run_step(db, label: str, fn) -> bool:
    """Roda uma etapa isolada. Em caso de erro, desfaz a transacao pendente dessa
    etapa (pra nao deixar a sessao "suja" pras proximas) e continua o resto."""
    try:
        result = fn()
        print(f"  {label}: {result}")
        return True
    except Exception as exc:  # noqa: BLE001 - queremos seguir mesmo com falha pontual
        db.rollback()
        print(f"  {label}: FALHOU -- {exc}")
        traceback.print_exc()
        return False


def _sync_meta_ads(full: bool) -> bool:
    """Sincroniza Meta Ads se META_ACCESS_TOKEN estiver configurado. Devolve True
    tanto quando tudo vai bem QUANTO quando esta simplesmente desligado (sem
    credenciais) -- so devolve False quando esta configurado e algo falhou de
    verdade, pra nao acender alarme falso em quem ainda nao conectou o Meta."""
    if not settings.meta_access_token:
        print("  meta ads: pulado (META_ACCESS_TOKEN nao configurado)")
        return True

    ok = True
    with session_scope() as db:
        ok &= _run_step(db, "meta ads: campanhas", lambda: meta_sync.sync_campaigns(db))
        ok &= _run_step(db, "meta ads: conjuntos de anuncios", lambda: meta_sync.sync_adsets(db))
        ok &= _run_step(db, "meta ads: anuncios", lambda: meta_sync.sync_ads(db))
        if full:
            ok &= _run_step(db, "meta ads: performance (historico completo)", lambda: meta_sync.sync_insights(db))
        else:
            ok &= _run_step(
                db, "meta ads: performance (ultimos dias)", lambda: meta_sync.sync_insights_incremental(db)
            )
    return ok


def run_full_sync() -> set[str]:
    ok_entities: set[str] = set()

    with session_scope() as db:
        _run_step(db, "usuarios", lambda: users_sync.sync_users(db))

        def _pipelines():
            p, s = pipelines_sync.sync_pipelines_and_stages(db)
            return f"{p} pipelines, {s} etapas"

        _run_step(db, "pipelines/etapas", _pipelines)
        _run_step(db, "motivos de perda", lambda: lost_reasons_sync.sync_lost_reasons(db))
        _run_step(db, "origens", lambda: sources_sync.sync_deal_sources(db))
        _run_step(db, "campanhas", lambda: sources_sync.sync_campaigns(db))

        if _run_step(db, "empresas", lambda: organizations_sync.sync_organizations(db)):
            ok_entities.add("organizations")
        if _run_step(db, "contatos", lambda: contacts_sync.sync_contacts(db)):
            ok_entities.add("contacts")
        if _run_step(db, "negociacoes", lambda: deals_sync.sync_deals_full(db)):
            ok_entities.add("deals")
        if _run_step(db, "tarefas", lambda: tasks_sync.sync_tasks(db)):
            ok_entities.add("tasks")
        if _run_step(db, "reunioes", lambda: meetings_sync.sync_meetings(db)):
            ok_entities.add("meetings")

    now = datetime.now(timezone.utc)
    with session_scope() as db:
        for entity in ok_entities:
            _set_last_synced_at(db, entity, now)

    meta_ok = _sync_meta_ads(full=True)

    faltando = set(INCREMENTAL_ENTITIES) - ok_entities
    if not meta_ok:
        faltando.add("meta_ads")
    if faltando:
        print(f"Carga inicial concluida com pendencias em: {', '.join(sorted(faltando))}.")
    else:
        print("Carga inicial concluida.")
    return faltando


def run_incremental_sync() -> set[str]:
    now = datetime.now(timezone.utc)
    ok_entities: set[str] = set()

    with session_scope() as db:
        # Cadastros pequenos: sempre re-sincroniza por completo, e nao por delta.
        _run_step(db, "usuarios", lambda: users_sync.sync_users(db))

        def _pipelines():
            p, s = pipelines_sync.sync_pipelines_and_stages(db)
            return f"{p} pipelines, {s} etapas"

        _run_step(db, "pipelines/etapas", _pipelines)
        _run_step(db, "motivos de perda", lambda: lost_reasons_sync.sync_lost_reasons(db))
        _run_step(db, "origens", lambda: sources_sync.sync_deal_sources(db))
        _run_step(db, "campanhas", lambda: sources_sync.sync_campaigns(db))

        since = _get_last_synced_at(db, "organizations")
        since_iso = since.isoformat(timespec="seconds") if since else None
        if _run_step(db, "empresas atualizadas", lambda: organizations_sync.sync_organizations(db, updated_since=since_iso)):
            ok_entities.add("organizations")

        since = _get_last_synced_at(db, "contacts")
        since_iso = since.isoformat(timespec="seconds") if since else None
        if _run_step(db, "contatos atualizados", lambda: contacts_sync.sync_contacts(db, updated_since=since_iso)):
            ok_entities.add("contacts")

        since = _get_last_synced_at(db, "deals")
        if since is None:
            if _run_step(db, "negociacoes (full)", lambda: deals_sync.sync_deals_full(db)):
                ok_entities.add("deals")
        else:
            since_iso = since.isoformat(timespec="seconds")
            if _run_step(db, "negociacoes atualizadas", lambda: deals_sync.sync_deals_incremental(db, updated_since_iso=since_iso)):
                ok_entities.add("deals")

        since = _get_last_synced_at(db, "tasks")
        since_iso = since.isoformat(timespec="seconds") if since else None
        if _run_step(db, "tarefas atualizadas", lambda: tasks_sync.sync_tasks(db, updated_since=since_iso)):
            ok_entities.add("tasks")

        since = _get_last_synced_at(db, "meetings")
        since_iso = since.isoformat(timespec="seconds") if since else None
        if _run_step(db, "reunioes atualizadas", lambda: meetings_sync.sync_meetings(db, updated_since=since_iso)):
            ok_entities.add("meetings")

    with session_scope() as db:
        for entity in ok_entities:
            _set_last_synced_at(db, entity, now)

    meta_ok = _sync_meta_ads(full=False)

    faltando = set(INCREMENTAL_ENTITIES) - ok_entities
    if not meta_ok:
        faltando.add("meta_ads")
    if faltando:
        print(f"Sincronizacao incremental concluida com pendencias em: {', '.join(sorted(faltando))}.")
    else:
        print("Sincronizacao incremental concluida.")
    return faltando


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "incremental"
    if mode == "full":
        faltando = run_full_sync()
    elif mode == "incremental":
        faltando = run_incremental_sync()
    else:
        print("Uso: python -m ingestion.sync_all [full|incremental]")
        sys.exit(1)

    # Sai com codigo != 0 quando alguma entidade falhou. `_run_step` engole a excecao
    # de proposito (pra uma entidade instavel nao derrubar as outras), mas sem isto o
    # processo terminaria 0 mesmo com tudo quebrado -- e o agendador (GitHub Actions)
    # mostraria verde numa rodada que nao sincronizou nada.
    if faltando:
        sys.exit(1)
