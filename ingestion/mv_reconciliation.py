"""
Reconciliacao das campanhas semanais do Melhor Venda (MV) contra o RD CRM.

O MV nao tem API/webhook integrado aqui -- os dados chegam via print que o usuario
manda toda semana. Este modulo:
1. registra a campanha e a lista de empresas (nome + status no MV)
2. tenta casar cada empresa com uma negociacao do CRM que veio com origem "Melhor
   Venda" (source_id) e foi criada dentro da janela da campanha, usando similaridade
   de nome como criterio de desempate
3. deixa como pendente (sem match automatico) qualquer caso ambiguo ou sem
   correspondencia, para revisao manual -- nunca "chuta" um match duvidoso

Uso tipico (chamado a partir de um script/chat, nao tem CLI proprio ainda):

    from ingestion.mv_reconciliation import create_campaign, add_companies, auto_match_campaign

    campaign_id = create_campaign(date(2026, 8, 11), date(2026, 8, 15))
    add_companies(campaign_id, [("Empresa X Telecom", "Conectado"), ...])
    auto_match_campaign(campaign_id, mv_source_id="6a39411c5945e80029aa36ea")
"""

import re
from datetime import date, timedelta
from difflib import SequenceMatcher

from database.connection import session_scope
from database.models import CrmDeal, CrmOrganization, MvCampaign, MvCampaignCompany

_SUFFIXES = re.compile(r"\b(ltda|me|epp|s ?/ ?a|sa|eireli|mei)\b\.?", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]")
_MULTI_SPACE = re.compile(r"\s+")


def _normalize(name: str) -> str:
    name = name.lower()
    name = _SUFFIXES.sub("", name)
    name = _NON_ALNUM.sub(" ", name)
    return _MULTI_SPACE.sub(" ", name).strip()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def create_campaign(week_start: date, week_end: date, notes: str | None = None) -> str:
    with session_scope() as db:
        campaign = MvCampaign(week_start=week_start, week_end=week_end, notes=notes)
        db.add(campaign)
        db.flush()
        return campaign.id


def add_companies(campaign_id: str, companies: list[tuple[str, str | None]]) -> int:
    """companies: lista de (nome_no_mv, status_no_mv)."""
    count = 0
    with session_scope() as db:
        for name, status in companies:
            db.add(MvCampaignCompany(campaign_id=campaign_id, company_name_mv=name, mv_status=status))
            count += 1
    return count


def auto_match_campaign(
    campaign_id: str,
    mv_source_id: str,
    days_buffer: int = 3,
    min_similarity: float = 0.6,
    ambiguous_margin: float = 0.1,
) -> dict:
    """Casa empresas da campanha com negociacoes do CRM. So marca match_confidence=
    'auto_source' quando ha um candidato claramente melhor que os demais; caso
    contrario deixa pendente (match_confidence None) para revisao manual."""
    results = {"matched": 0, "ambiguous": 0, "unmatched": 0}

    with session_scope() as db:
        campaign = db.get(MvCampaign, campaign_id)
        window_start = campaign.week_start - timedelta(days=days_buffer)
        window_end = campaign.week_end + timedelta(days=days_buffer)

        candidates = (
            db.query(CrmDeal)
            .filter(
                CrmDeal.source == mv_source_id,
                CrmDeal.deal_created_at >= window_start,
                CrmDeal.deal_created_at <= window_end,
            )
            .all()
        )

        org_ids = [c.organization_rd_id for c in candidates if c.organization_rd_id]
        orgs = {
            o.rd_id: o
            for o in db.query(CrmOrganization).filter(CrmOrganization.rd_id.in_(org_ids)).all()
        } if org_ids else {}

        pending = (
            db.query(MvCampaignCompany)
            .filter(
                MvCampaignCompany.campaign_id == campaign_id,
                MvCampaignCompany.matched_deal_rd_id.is_(None),
            )
            .all()
        )

        for company in pending:
            scored: list[tuple[float, CrmDeal]] = []
            for deal in candidates:
                org = orgs.get(deal.organization_rd_id)
                names = [deal.name, org.name if org else None]
                best = max((_similarity(company.company_name_mv, n) for n in names if n), default=0.0)
                if best >= min_similarity:
                    scored.append((best, deal))

            scored.sort(key=lambda pair: pair[0], reverse=True)

            if not scored:
                results["unmatched"] += 1
                continue

            if len(scored) > 1 and (scored[0][0] - scored[1][0]) < ambiguous_margin:
                results["ambiguous"] += 1
                note = f"[auto] {len(scored)} candidatos ambiguos (scores proximos) -- revisar manualmente"
                company.notes = f"{company.notes}\n{note}" if company.notes else note
                continue

            _, best_deal = scored[0]
            company.matched_deal_rd_id = best_deal.rd_id
            company.matched_organization_rd_id = best_deal.organization_rd_id
            company.match_confidence = "auto_source"
            results["matched"] += 1

    return results
