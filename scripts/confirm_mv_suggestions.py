"""
Confirma (ou rejeita) sugestoes pendentes de uma campanha do Melhor Venda.

Uso:
    python -m scripts.confirm_mv_suggestions "Agosto/Semana 1"              # confirma todas as sugestoes pendentes
    python -m scripts.confirm_mv_suggestions "Agosto/Semana 1" --reject ID1 ID2   # rejeita ids especificos, confirma o resto
"""

import sys

from database.connection import session_scope
from database.models import MvCampaign, MvCampaignCompany
from ingestion.mv_reconciliation import confirm_suggestion, reject_suggestion


def _find_campaign_id(label_or_id: str) -> str:
    with session_scope() as db:
        campaign = (
            db.query(MvCampaign)
            .filter((MvCampaign.label == label_or_id) | (MvCampaign.id == label_or_id))
            .one_or_none()
        )
        if campaign is None:
            raise SystemExit(f"Campanha '{label_or_id}' nao encontrada.")
        return campaign.id


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Uso: python -m scripts.confirm_mv_suggestions "Agosto/Semana 1" [--reject ID1 ID2 ...]')
        sys.exit(1)

    campaign_id = _find_campaign_id(sys.argv[1])

    reject_ids: set[str] = set()
    if "--reject" in sys.argv:
        idx = sys.argv.index("--reject")
        reject_ids = set(sys.argv[idx + 1:])

    with session_scope() as db:
        pending = (
            db.query(MvCampaignCompany)
            .filter(
                MvCampaignCompany.campaign_id == campaign_id,
                MvCampaignCompany.suggested_deal_rd_id.isnot(None),
                MvCampaignCompany.matched_deal_rd_id.is_(None),
            )
            .all()
        )
        ids = [row.id for row in pending]

    confirmed, rejected = 0, 0
    for company_id in ids:
        if company_id in reject_ids:
            reject_suggestion(company_id)
            rejected += 1
        else:
            confirm_suggestion(company_id)
            confirmed += 1

    print(f"{confirmed} sugestoes confirmadas, {rejected} rejeitadas.")
