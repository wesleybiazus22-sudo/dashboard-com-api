"""
Preenche sdr_owner_rd_id/closer_owner_rd_id retroativamente para negociacoes que ja
existiam antes do webhook ser cadastrado (ver scripts/register_webhooks.py).

Usa exatamente a mesma logica do processor de webhooks (primeiro dono no historico =
SDR, primeiro dono diferente depois = closer), aplicada sobre o crm_deal_owner_history
que ja temos -- para a maioria das negociacoes isso e so 1 linha (o "snapshot" salvo
na primeira sincronizacao), entao o resultado e o dono de quando sincronizamos pela
primeira vez. Se o handoff SDR->closer ja tinha acontecido ANTES da primeira
sincronizacao, nao tem como recuperar isso aqui -- so o webhook capturando handoffs
dai pra frente resolve de verdade.

Uso: python -m scripts.backfill_sdr_closer
"""

from database.connection import session_scope
from database.models import CrmDeal
from webhooks.processor import apply_sdr_closer_split

if __name__ == "__main__":
    with session_scope() as db:
        deals = db.query(CrmDeal).all()
        atualizados = 0
        for deal in deals:
            before = (deal.sdr_owner_rd_id, deal.closer_owner_rd_id)
            apply_sdr_closer_split(db, deal, deal.rd_id)
            if (deal.sdr_owner_rd_id, deal.closer_owner_rd_id) != before:
                atualizados += 1

    print(f"{len(deals)} negociacoes verificadas, {atualizados} atualizadas.")
