"""
Reparo pontual (uma vez so) do historico de etapa/dono que ficou "congelado" antes da
correcao em ingestion/rd_crm/deal_history.py.

Contexto: ate essa correcao, a linha de crm_deal_stage_history/crm_deal_owner_history
de cada negociacao nunca era fechada/reaberta quando a negociacao avancava de etapa ou
trocava de dono -- ficava presa no estado da primeira vez que a negociacao foi
sincronizada. Isso significa que, pra negociacoes que ja avancaram desde entao, a linha
"aberta" (exited_at/unassigned_at nulos) esta mentindo: tanto a ETAPA quanto o
entered_at registrados nela nao refletem mais a realidade.

Nao da pra reconstruir o tempo real gasto em cada etapa intermediaria que passou
despercebida -- esse dado se perdeu (mesma limitacao ja documentada pro handoff
SDR->closer). O que este script faz e o proximo melhor: fecha a linha desatualizada
"agora" e abre uma nova linha, com a etapa/dono CORRETOS (vindos de crm_deals, que
sempre fica correto -- so o historico que nao acompanhava) e entered_at = agora. A
partir da a sincronizacao normal (ja corrigida) volta a rastrear certinho toda
transicao futura.

Efeito colateral aceito: o card de "tempo parado na etapa atual" zera pra essas
negociacoes reparadas (comeca a contar de novo a partir de agora), em vez de mostrar um
numero relativo (e errado) desde a data de criacao original.

Uso: python -m scripts.repair_stage_history
"""

from datetime import datetime, timezone

from database.connection import session_scope
from database.models import CrmDeal, CrmDealOwnerHistory, CrmDealStageHistory
from ingestion.rd_crm.deal_history import apply_sdr_closer_split

if __name__ == "__main__":
    now = datetime.now(timezone.utc)

    with session_scope() as db:
        deals = db.query(CrmDeal).all()
        stage_fixed = 0
        owner_fixed = 0

        for deal in deals:
            open_stage = (
                db.query(CrmDealStageHistory)
                .filter(CrmDealStageHistory.deal_rd_id == deal.rd_id, CrmDealStageHistory.exited_at.is_(None))
                .order_by(CrmDealStageHistory.entered_at.desc())
                .first()
            )
            if deal.stage_rd_id and (open_stage is None or open_stage.stage_rd_id != deal.stage_rd_id):
                if open_stage:
                    open_stage.exited_at = now
                    open_stage.duration_seconds = int((now - open_stage.entered_at).total_seconds())
                db.add(
                    CrmDealStageHistory(
                        deal_id=deal.id,
                        deal_rd_id=deal.rd_id,
                        stage_rd_id=deal.stage_rd_id,
                        pipeline_rd_id=deal.pipeline_rd_id,
                        owner_rd_id=deal.current_owner_rd_id,
                        entered_at=now,
                    )
                )
                stage_fixed += 1

            open_owner = (
                db.query(CrmDealOwnerHistory)
                .filter(CrmDealOwnerHistory.deal_rd_id == deal.rd_id, CrmDealOwnerHistory.unassigned_at.is_(None))
                .order_by(CrmDealOwnerHistory.assigned_at.desc())
                .first()
            )
            if deal.current_owner_rd_id and (open_owner is None or open_owner.owner_rd_id != deal.current_owner_rd_id):
                if open_owner:
                    open_owner.unassigned_at = now
                db.add(
                    CrmDealOwnerHistory(
                        deal_id=deal.id,
                        deal_rd_id=deal.rd_id,
                        owner_rd_id=deal.current_owner_rd_id,
                        assigned_at=now,
                    )
                )
                owner_fixed += 1

            db.flush()
            apply_sdr_closer_split(db, deal, deal.rd_id)

        print(
            f"{len(deals)} negociacoes verificadas, "
            f"{stage_fixed} historicos de etapa corrigidos, "
            f"{owner_fixed} historicos de dono corrigidos."
        )
