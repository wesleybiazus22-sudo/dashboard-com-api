"""
Reconciliacao das campanhas semanais do Melhor Venda (MV) contra o RD CRM.

O MV nao tem API/webhook integrado aqui -- os dados chegam via print que o usuario
manda toda semana. Este modulo:
1. registra a campanha e a lista de empresas (nome + CNPJ + status no MV)
2. tenta casar cada empresa com uma negociacao do CRM que veio com origem "Melhor
   Venda" (source_id) e foi criada dentro da janela da campanha -- primeiro por CNPJ
   (se o CNPJ aparecer em qualquer lugar dos dados da empresa/negociacao no CRM, e
   correspondencia praticamente certa, mesmo sem saber o nome exato do campo onde o
   RD guarda isso), com similaridade de nome como fallback quando nao ha CNPJ
3. deixa como pendente (sem match automatico) qualquer caso ambiguo ou sem
   correspondencia, para revisao manual -- nunca "chuta" um match duvidoso

Uso tipico (chamado a partir de um script/chat, nao tem CLI proprio ainda):

    from ingestion.mv_reconciliation import create_campaign, add_companies, auto_match_campaign

    campaign_id = create_campaign(date(2026, 8, 4), date(2026, 8, 7), sdr_name="Miriã", label="Agosto/Semana 1")
    add_companies(campaign_id, [("Empresa X Telecom", "11.185.012/0001-54", "Conectado"), ...])
    auto_match_campaign(campaign_id)  # usa MV_SOURCE_ID por padrao
"""

import json
import re
from datetime import date, timedelta
from difflib import SequenceMatcher

from database.connection import session_scope
from database.models import CrmDeal, CrmOrganization, MvCampaign, MvCampaignCompany

# Confirmado no RD Station (campo "Fonte" = "Melhor Venda" na negociacao).
MV_SOURCE_ID = "6a39411c5945e80029aa36ea"

_SUFFIXES = re.compile(r"\b(ltda|me|epp|s ?/ ?a|sa|eireli|mei)\b\.?", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]")
_MULTI_SPACE = re.compile(r"\s+")
_NON_DIGIT = re.compile(r"\D")


def _normalize(name: str) -> str:
    name = name.lower()
    name = _SUFFIXES.sub("", name)
    name = _NON_ALNUM.sub(" ", name)
    return _MULTI_SPACE.sub(" ", name).strip()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _digits(value: str | None) -> str:
    return _NON_DIGIT.sub("", value) if value else ""


def create_campaign(
    week_start: date,
    week_end: date,
    sdr_name: str | None = None,
    label: str | None = None,
    notes: str | None = None,
) -> str:
    with session_scope() as db:
        campaign = MvCampaign(
            week_start=week_start, week_end=week_end, sdr_name=sdr_name, label=label, notes=notes
        )
        db.add(campaign)
        db.flush()
        return campaign.id


def add_companies(campaign_id: str, companies: list[tuple[str, str | None, str | None]]) -> int:
    """companies: lista de (nome_no_mv, cnpj_no_mv, status_no_mv)."""
    count = 0
    with session_scope() as db:
        for name, cnpj, status in companies:
            db.add(
                MvCampaignCompany(
                    campaign_id=campaign_id,
                    company_name_mv=name,
                    cnpj_mv=cnpj,
                    mv_status=status,
                )
            )
            count += 1
    return count


def auto_match_campaign(
    campaign_id: str,
    mv_source_id: str = MV_SOURCE_ID,
    days_buffer: int = 3,
    min_similarity: float = 0.6,
    ambiguous_margin: float = 0.1,
) -> dict:
    """Casa empresas da campanha com negociacoes do CRM.

    Prioridade: (1) CNPJ encontrado em qualquer lugar do raw da negociacao/empresa no
    CRM -> match_confidence='auto_cnpj', decisivo, ignora ambiguidade de nome.
    (2) similaridade de nome -> match_confidence='auto_name', so quando ha um
    candidato claramente melhor que os demais.
    Caso contrario fica pendente (match_confidence None) para revisao manual.
    """
    results = {"matched_cnpj": 0, "matched_name": 0, "ambiguous": 0, "unmatched": 0}

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

        # Pre-computa a "sopa de digitos" de cada negociacao candidata (raw do deal +
        # raw da empresa vinculada), pra buscar o CNPJ sem depender de saber o nome
        # exato do campo onde o RD guarda isso.
        deal_digit_blobs: dict[str, str] = {}
        for deal in candidates:
            org = orgs.get(deal.organization_rd_id)
            blob = json.dumps(deal.raw or {}) + json.dumps(org.raw if org else {})
            deal_digit_blobs[deal.rd_id] = _digits(blob)

        pending = (
            db.query(MvCampaignCompany)
            .filter(
                MvCampaignCompany.campaign_id == campaign_id,
                MvCampaignCompany.matched_deal_rd_id.is_(None),
            )
            .all()
        )

        for company in pending:
            cnpj_digits = _digits(company.cnpj_mv)
            cnpj_matches = (
                [d for d in candidates if cnpj_digits and cnpj_digits in deal_digit_blobs[d.rd_id]]
                if cnpj_digits
                else []
            )

            if len(cnpj_matches) == 1:
                deal = cnpj_matches[0]
                company.matched_deal_rd_id = deal.rd_id
                company.matched_organization_rd_id = deal.organization_rd_id
                company.match_confidence = "auto_cnpj"
                results["matched_cnpj"] += 1
                continue

            if len(cnpj_matches) > 1:
                results["ambiguous"] += 1
                note = f"[auto] CNPJ {company.cnpj_mv} bateu em {len(cnpj_matches)} negociacoes -- revisar manualmente"
                company.notes = f"{company.notes}\n{note}" if company.notes else note
                continue

            # Sem match por CNPJ -- cai pro fallback de similaridade de nome.
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
                note = f"[auto] {len(scored)} candidatos ambiguos por nome (scores proximos) -- revisar manualmente"
                company.notes = f"{company.notes}\n{note}" if company.notes else note
                continue

            _, best_deal = scored[0]
            company.matched_deal_rd_id = best_deal.rd_id
            company.matched_organization_rd_id = best_deal.organization_rd_id
            company.match_confidence = "auto_name"
            results["matched_name"] += 1

    return results
