"""
Re-roda o cruzamento automatico de uma campanha do Melhor Venda ja existente (sem
recriar as empresas -- so reprocessa quem ainda esta sem match confirmado). Util
depois de corrigir dados no RD Station (ex: origem de uma negociacao) e rodar
`python -m ingestion.sync_all incremental` pra trazer a atualizacao.

Uso: python -m scripts.rematch_mv_campaign "Agosto/Semana 1"
     python -m scripts.rematch_mv_campaign <campaign_id>
"""

import sys

from database.connection import session_scope
from database.models import CrmDeal, CrmStage, MvCampaign, MvCampaignCompany
from ingestion.mv_reconciliation import auto_match_campaign


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


def _print_report(campaign_id: str) -> None:
    with session_scope() as db:
        rows = (
            db.query(MvCampaignCompany)
            .filter(MvCampaignCompany.campaign_id == campaign_id)
            .order_by(MvCampaignCompany.company_name_mv)
            .all()
        )
        print("\n--- Resultado do cruzamento ---")
        for row in rows:
            if row.matched_deal_rd_id:
                deal = db.query(CrmDeal).filter(CrmDeal.rd_id == row.matched_deal_rd_id).one_or_none()
                stage = (
                    db.query(CrmStage).filter(CrmStage.rd_id == deal.stage_rd_id).one_or_none()
                    if deal else None
                )
                stage_name = stage.name if stage and stage.name else "(etapa sem nome)"
                print(
                    f"  [{row.match_confidence:11}] {row.company_name_mv:45} -> "
                    f"{deal.name if deal else '?':35} | {stage_name} | status={deal.status if deal else '?'}"
                )
            elif row.suggested_deal_rd_id:
                deal = db.query(CrmDeal).filter(CrmDeal.rd_id == row.suggested_deal_rd_id).one_or_none()
                print(
                    f"  [SUGERIDO   ] {row.company_name_mv:45} -> "
                    f"{deal.name if deal else '?':35} | score={row.suggested_score} | id={row.id}"
                )
            else:
                flag = "AMBIGUO" if row.notes and "ambiguos" in row.notes else "SEM MATCH"
                print(f"  [{flag:11}] {row.company_name_mv:45} (MV: {row.mv_status})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Uso: python -m scripts.rematch_mv_campaign "Agosto/Semana 1"')
        sys.exit(1)

    campaign_id = _find_campaign_id(sys.argv[1])
    results = auto_match_campaign(campaign_id)
    print(
        f"Cruzamento: {results['matched_cnpj']} confirmadas por CNPJ, "
        f"{results['suggested']} sugeridas por nome, "
        f"{results['ambiguous']} ambiguas, {results['unmatched']} sem match."
    )
    _print_report(campaign_id)
