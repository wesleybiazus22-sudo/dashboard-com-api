"""
Sincronizacao do Meta Ads: estrutura (campanha/conjunto/anuncio) + performance
diaria (insights).

Estrategia de janela dos insights -- a parte que mais foge do padrao usado com o
RD CRM: o Meta pode REVISAR metricas de conversao de um anuncio por ate alguns dias
apos o fato, por causa da janela de atribuicao (padrao: 7 dias de clique, 1 dia de
visualizacao) -- uma pessoa pode ver o anuncio hoje e converter daqui a 5 dias, e
esse resultado so aparece nos numeros de HOJE quando a API for consultada de novo
depois. Por isso a sincronizacao incremental NAO pega "so o que e novo": ela
RE-BUSCA uma janela rolante dos ultimos dias a cada rodada, sobrescrevendo os
valores antigos (upsert idempotente por (ad_id, data)). Sem isso, o numero de
conversoes de ontem ficaria congelado no valor (menor) que existia ontem mesmo.
"""

import json
from datetime import date, timedelta

from sqlalchemy.orm import Session

from database.models import MetaAd, MetaAdSet, MetaCampaign, MetaInsightDaily
from ingestion.meta_ads.client import MetaAdsClient
from ingestion.meta_ads.entities import (
    parse_action_sum,
    parse_date,
    parse_dt,
    parse_int,
    parse_money,
    upsert_by_meta_id,
    upsert_insight_row,
)

# Dias pra tras que a sincronizacao incremental re-busca a cada rodada, pra
# absorver revisao de atribuicao (ver docstring do modulo).
JANELA_INCREMENTAL_DIAS = 8

CAMPAIGN_FIELDS = "id,name,objective,status,effective_status,daily_budget,lifetime_budget,start_time,stop_time"
ADSET_FIELDS = "id,name,campaign_id,status,effective_status,daily_budget,lifetime_budget"
AD_FIELDS = "id,name,adset_id,campaign_id,status,effective_status,creative{thumbnail_url}"
INSIGHT_FIELDS = (
    "campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,"
    "spend,impressions,clicks,reach,frequency,ctr,cpc,cpm,actions,cost_per_action_type,"
    # Campos de engajamento de video -- NAO vem dentro do array generico `actions`,
    # precisam ser pedidos por nome. video_p50_watched_actions = "assistiu pelo
    # menos 50% do video" (o "View 50%" do Ads Manager); video_thruplay_watched_actions
    # = ThruPlay (assistiu inteiro, ou 15s+ pra videos mais longos).
    "video_thruplay_watched_actions,video_p50_watched_actions,"
    "date_start,date_stop"
)


def sync_campaigns(db: Session) -> int:
    client = MetaAdsClient()
    count = 0
    for item in client.paginate(f"/{client.ad_account_id}/campaigns", {"fields": CAMPAIGN_FIELDS, "limit": 200}):
        upsert_by_meta_id(
            db, MetaCampaign, item["id"],
            {
                "name": item.get("name"),
                "objective": item.get("objective"),
                "status": item.get("status"),
                "effective_status": item.get("effective_status"),
                "daily_budget": parse_money(item.get("daily_budget")),
                "lifetime_budget": parse_money(item.get("lifetime_budget")),
                "start_time": parse_dt(item.get("start_time")),
                "stop_time": parse_dt(item.get("stop_time")),
                "raw": item,
            },
        )
        count += 1
    db.commit()
    return count


def sync_adsets(db: Session) -> int:
    client = MetaAdsClient()
    count = 0
    for item in client.paginate(f"/{client.ad_account_id}/adsets", {"fields": ADSET_FIELDS, "limit": 200}):
        upsert_by_meta_id(
            db, MetaAdSet, item["id"],
            {
                "campaign_meta_id": item.get("campaign_id"),
                "name": item.get("name"),
                "status": item.get("status"),
                "effective_status": item.get("effective_status"),
                "daily_budget": parse_money(item.get("daily_budget")),
                "lifetime_budget": parse_money(item.get("lifetime_budget")),
                "raw": item,
            },
        )
        count += 1
    db.commit()
    return count


def sync_ads(db: Session) -> int:
    client = MetaAdsClient()
    count = 0
    for item in client.paginate(f"/{client.ad_account_id}/ads", {"fields": AD_FIELDS, "limit": 200}):
        creative = item.get("creative") or {}
        upsert_by_meta_id(
            db, MetaAd, item["id"],
            {
                "adset_meta_id": item.get("adset_id"),
                "campaign_meta_id": item.get("campaign_id"),
                "name": item.get("name"),
                "status": item.get("status"),
                "effective_status": item.get("effective_status"),
                "creative_thumbnail_url": creative.get("thumbnail_url"),
                "raw": item,
            },
        )
        count += 1
    db.commit()
    return count


def sync_insights(db: Session, since: date | None = None, until: date | None = None) -> int:
    """Sincroniza performance diaria no nivel de anuncio. Sem `since`/`until`,
    busca a vida inteira da conta (`date_preset=maximum`, ate ~37 meses)."""
    client = MetaAdsClient()
    params = {"level": "ad", "time_increment": 1, "fields": INSIGHT_FIELDS, "limit": 500}
    if since and until:
        params["time_range"] = json.dumps({"since": since.isoformat(), "until": until.isoformat()})
    else:
        params["date_preset"] = "maximum"

    count = 0
    for item in client.paginate(f"/{client.ad_account_id}/insights", params):
        dia = parse_date(item.get("date_start"))
        ad_id = item.get("ad_id")
        if dia is None or not ad_id:
            continue  # linha sem data/anuncio identificavel -- nao da pra formar a chave natural
        upsert_insight_row(
            db, MetaInsightDaily, ad_id, dia,
            {
                "campaign_meta_id": item.get("campaign_id"),
                "campaign_name": item.get("campaign_name"),
                "adset_meta_id": item.get("adset_id"),
                "adset_name": item.get("adset_name"),
                "ad_name": item.get("ad_name"),
                "spend": parse_money(item.get("spend")),
                "impressions": parse_int(item.get("impressions")),
                "clicks": parse_int(item.get("clicks")),
                "reach": parse_int(item.get("reach")),
                "frequency": parse_money(item.get("frequency")),
                "ctr": parse_money(item.get("ctr")),
                "cpc": parse_money(item.get("cpc")),
                "cpm": parse_money(item.get("cpm")),
                "actions": item.get("actions"),
                "cost_per_action_type": item.get("cost_per_action_type"),
                "video_thruplay": parse_action_sum(item.get("video_thruplay_watched_actions")),
                "video_view_50": parse_action_sum(item.get("video_p50_watched_actions")),
                "raw": item,
            },
        )
        count += 1
    db.commit()
    return count


def sync_insights_incremental(db: Session) -> int:
    """Janela rolante dos ultimos `JANELA_INCREMENTAL_DIAS` dias -- ver docstring
    do modulo sobre por que isso re-busca em vez de so pegar o que e novo."""
    hoje = date.today()
    return sync_insights(db, since=hoje - timedelta(days=JANELA_INCREMENTAL_DIAS - 1), until=hoje)
