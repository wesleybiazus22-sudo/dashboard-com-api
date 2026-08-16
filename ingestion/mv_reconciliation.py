"""
Reconciliacao das campanhas semanais do Melhor Venda (MV) contra o RD CRM.

O MV nao tem API/webhook integrado aqui -- os dados chegam via print que o usuario
manda toda semana. Este modulo:
1. registra a campanha e a lista de empresas (nome + CNPJ + status no MV)
2. tenta casar cada empresa com uma negociacao do CRM que veio com origem "Melhor
   Venda" (source_id) e foi criada dentro da janela da campanha
3. so CONFIRMA automaticamente (matched_*) por CNPJ -- se o CNPJ aparecer em
   qualquer lugar dos dados da negociacao/empresa no CRM, e correspondencia
   praticamente certa. Similaridade de nome NUNCA confirma sozinha: nesse nicho
   (ISP/telecom) quase todo nome compartilha palavras genericas ("telecom",
   "internet", "net", "fibra", "provedor"...), o que gera falsos positivos faceis
   (ja aconteceu: "OPTTO TELECOMUNICACOES" e "W R TELECOMUNICACAO" bateram os dois
   com "KFM TELECOMUNICACOES" so por causa do sufixo comum). Nome vira SUGESTAO
   (suggested_*), que precisa de confirm_suggestion() explicito.

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

# Termos genericos do nicho ISP/telecom -- removidos ANTES de calcular similaridade,
# senao dois nomes de empresas totalmente diferentes (ex: "Optto Telecomunicacoes"
# vs "KFM Telecomunicacoes") ficam parecidos so pelo sufixo comum do setor.
_GENERIC_TERMS = re.compile(
    r"\b("
    r"ltda|me|epp|s ?/ ?a|sa|eireli|mei|"
    r"telecom(unicacoes?|unicacao)?|internet|provedor(a|es)?|fibra|conexao|conexoes|"
    r"comunicacoes?|net|sistema|system|digital|tecnologia|servicos?|comercio|"
    r"solucoes|valor|adicionado|central"
    r")\b\.?",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]")
_MULTI_SPACE = re.compile(r"\s+")
_NON_DIGIT = re.compile(r"\D")

MIN_DISTINCTIVE_LEN = 3  # abaixo disso, o nome ficou generico demais pra comparar


def _normalize(name: str) -> str:
    name = name.lower()
    name = _GENERIC_TERMS.sub("", name)
    name = _NON_ALNUM.sub(" ", name)
    return _MULTI_SPACE.sub(" ", name).strip()


def _similarity(a: str, b: str) -> float | None:
    """None quando algum dos dois nomes, apos remover termos genericos, ficou curto
    demais pra uma comparacao confiavel (evita falso positivo por sufixo comum)."""
    na, nb = _normalize(a), _normalize(b)
    if len(na) < MIN_DISTINCTIVE_LEN or len(nb) < MIN_DISTINCTIVE_LEN:
        return None
    return SequenceMatcher(None, na, nb).ratio()


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
    days_before: int = 1,
    days_after: int = 14,
    min_similarity: float = 0.82,
) -> dict:
    """CNPJ com 1 candidato -> confirma (matched_*, match_confidence='auto_cnpj').
    CNPJ com 2+ candidatos -> ambiguo, fica pendente.
    Sem CNPJ -> melhor candidato por nome (se acima de min_similarity e sem empate
    proximo) vira SUGESTAO (suggested_*), nunca confirma sozinho.
    Sem nenhum candidato -> sem match.

    A janela de datas e assimetrica: pouca folga antes do inicio da semana (deals
    quase nunca sao criados antes da campanha comecar), bastante folga depois --
    observado na pratica que o Melhor Venda continua tentando contato por 1-2
    semanas apos a campanha "gerada", entao o deal so aparece no CRM bem depois do
    fim nominal da semana.
    """
    results = {"matched_cnpj": 0, "suggested": 0, "ambiguous": 0, "unmatched": 0}

    with session_scope() as db:
        campaign = db.get(MvCampaign, campaign_id)
        window_start = campaign.week_start - timedelta(days=days_before)
        window_end = campaign.week_end + timedelta(days=days_after)

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

            # Sem CNPJ (ou nao encontrado) -- similaridade de nome vira sugestao, nao match.
            scored: list[tuple[float, CrmDeal]] = []
            for deal in candidates:
                org = orgs.get(deal.organization_rd_id)
                names = [deal.name, org.name if org else None]
                scores = [s for n in names if n for s in [_similarity(company.company_name_mv, n)] if s is not None]
                if scores:
                    best = max(scores)
                    if best >= min_similarity:
                        scored.append((best, deal))

            scored.sort(key=lambda pair: pair[0], reverse=True)

            if not scored:
                results["unmatched"] += 1
                continue

            best_score, best_deal = scored[0]
            company.suggested_deal_rd_id = best_deal.rd_id
            company.suggested_organization_rd_id = best_deal.organization_rd_id
            company.suggested_score = round(best_score, 3)
            results["suggested"] += 1

    return results


def confirm_suggestion(company_id: str) -> None:
    """Promove a sugestao (suggested_*) a match confirmado, apos revisao humana."""
    with session_scope() as db:
        company = db.get(MvCampaignCompany, company_id)
        company.matched_deal_rd_id = company.suggested_deal_rd_id
        company.matched_organization_rd_id = company.suggested_organization_rd_id
        company.match_confidence = "manual"


def reject_suggestion(company_id: str) -> None:
    """Descarta a sugestao (nome parecido mas nao e a mesma empresa)."""
    with session_scope() as db:
        company = db.get(MvCampaignCompany, company_id)
        company.suggested_deal_rd_id = None
        company.suggested_organization_rd_id = None
        company.suggested_score = None
