"""
Carrega uma campanha semanal do Melhor Venda a partir de um JSON e roda o
cruzamento automatico contra o CRM. Uso:

    python -m scripts.load_mv_campaign data/mv_campaigns/2026-08-04_miria.json

Formato esperado do JSON:
{
  "sdr_name": "Miriã",
  "label": "Agosto/Semana 1",
  "week_start": "2026-08-04",
  "week_end": "2026-08-07",
  "companies": [
    {"name": "MICRODATA TELECOM LTDA", "cnpj": "11.185.012/0001-54", "status": "Conectado"},
    ...
  ]
}
"""

import json
import sys
from datetime import date

from database.connection import session_scope
from database.models import CrmDeal, CrmStage, MvCampaignCompany
from ingestion.mv_reconciliation import add_companies, auto_match_campaign, create_campaign


def _print_report(campaign_id: str) -> None:
    with session_scope() as db:
        rows = (
            db.query(MvCampaignCompany)
            .filter(MvCampaignCompany.campaign_id == campaign_id)
            .order_by(MvCampaignCompany.match_confidence.is_(None), MvCampaignCompany.company_name_mv)
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
            else:
                flag = "AMBIGUO" if row.notes and "ambiguos" in row.notes else "SEM MATCH"
                print(f"  [{flag:11}] {row.company_name_mv:45} (MV: {row.mv_status})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python -m scripts.load_mv_campaign <caminho_para_o_json>")
        sys.exit(1)

    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    campaign_id = create_campaign(
        week_start=date.fromisoformat(data["week_start"]),
        week_end=date.fromisoformat(data["week_end"]),
        sdr_name=data.get("sdr_name"),
        label=data.get("label"),
    )

    companies = [(c["name"], c.get("cnpj"), c.get("status")) for c in data["companies"]]
    n = add_companies(campaign_id, companies)
    print(f"{n} empresas registradas na campanha {data.get('label') or campaign_id}.")

    results = auto_match_campaign(campaign_id)
    print(
        f"Cruzamento: {results['matched_cnpj']} por CNPJ, {results['matched_name']} por nome, "
        f"{results['ambiguous']} ambiguos, {results['unmatched']} sem match."
    )

    _print_report(campaign_id)
