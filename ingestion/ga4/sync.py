"""
Sincronizacao dos relatorios da GA4 Data API: visao geral diaria, origem de
trafego, paginas, dispositivo e geografia.

Janela de sincronizacao -- mesmo raciocinio do Meta Ads (ver docstring de
ingestion/meta_ads/sync.py): o GA4 tambem pode revisar/consolidar metricas dos
ultimos dias por causa de processamento assincrono (o dia de "hoje"/"ontem" ainda
nao fechou de verdade). Por isso a sincronizacao incremental RE-BUSCA uma janela
rolante dos ultimos dias a cada rodada, em vez de so pegar o que e novo -- sem
isso, o numero de sessoes de ontem ficaria congelado no valor (menor) que existia
ontem mesmo.
"""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from database.models import (
    Ga4DailyOverview,
    Ga4DeviceDaily,
    Ga4GeoDaily,
    Ga4PageDaily,
    Ga4TrafficSourceDaily,
    Ga4UtmCampaignDaily,
)
from ingestion.ga4.client import Ga4Client
from ingestion.ga4.entities import clean_utm, parse_float, parse_ga4_date, parse_int, upsert_by_composite

# Dias pra tras que a sincronizacao incremental re-busca a cada rodada.
JANELA_INCREMENTAL_DIAS = 8

# Data mais antiga plausivel pra carga inicial -- o GA4 simplesmente devolve 0
# linhas pros dias anteriores ao inicio real da coleta na propriedade, entao um
# valor "grande demais" e inofensivo (so nao volta alem do que existe).
_INICIO_HISTORICO = "2020-01-01"


def _range_full() -> tuple[str, str]:
    return _INICIO_HISTORICO, "today"


def _range_incremental() -> tuple[str, str]:
    hoje = date.today()
    inicio = hoje - timedelta(days=JANELA_INCREMENTAL_DIAS - 1)
    return inicio.isoformat(), hoje.isoformat()


def sync_overview(db: Session, start_date: str, end_date: str) -> int:
    """Uma linha por dia: metricas-resumo do site inteiro."""
    client = Ga4Client()
    linhas = client.run_report(
        dimensions=["date"],
        metrics=[
            "sessions", "activeUsers", "newUsers", "engagedSessions", "engagementRate",
            "averageSessionDuration", "bounceRate", "screenPageViews", "eventCount", "keyEvents",
        ],
        start_date=start_date, end_date=end_date,
    )
    count = 0
    for item in linhas:
        dia = parse_ga4_date(item.get("date"))
        if dia is None:
            continue
        upsert_by_composite(
            db, Ga4DailyOverview, {"date": dia},
            {
                "sessions": parse_int(item.get("sessions")),
                "active_users": parse_int(item.get("activeUsers")),
                "new_users": parse_int(item.get("newUsers")),
                "engaged_sessions": parse_int(item.get("engagedSessions")),
                "engagement_rate": parse_float(item.get("engagementRate")),
                "avg_session_duration": parse_float(item.get("averageSessionDuration")),
                "bounce_rate": parse_float(item.get("bounceRate")),
                "screen_page_views": parse_int(item.get("screenPageViews")),
                "event_count": parse_int(item.get("eventCount")),
                "key_events": parse_int(item.get("keyEvents")),
            },
        )
        count += 1
    db.commit()
    return count


def sync_traffic_source(db: Session, start_date: str, end_date: str) -> int:
    """Uma linha por (dia, canal, origem, midia) -- de onde vem o trafego."""
    client = Ga4Client()
    linhas = client.run_report(
        dimensions=["date", "sessionDefaultChannelGroup", "sessionSource", "sessionMedium"],
        metrics=["sessions", "activeUsers", "engagedSessions", "keyEvents"],
        start_date=start_date, end_date=end_date,
    )
    count = 0
    for item in linhas:
        dia = parse_ga4_date(item.get("date"))
        if dia is None:
            continue
        chave = {
            "date": dia,
            "channel_group": item.get("sessionDefaultChannelGroup") or "(not set)",
            "source": item.get("sessionSource") or "(not set)",
            "medium": item.get("sessionMedium") or "(not set)",
        }
        upsert_by_composite(
            db, Ga4TrafficSourceDaily, chave,
            {
                "sessions": parse_int(item.get("sessions")),
                "active_users": parse_int(item.get("activeUsers")),
                "engaged_sessions": parse_int(item.get("engagedSessions")),
                "key_events": parse_int(item.get("keyEvents")),
            },
        )
        count += 1
    db.commit()
    return count


def sync_utm_campaign(db: Session, start_date: str, end_date: str) -> int:
    """Uma linha por (dia, canal, origem, midia, utm_campaign, utm_content) --
    quebra o trafego pelas TAGS DE UTM que a propria operacao coloca nos links dos
    anuncios, pra casar com precisao o nome exato de campanha/criativo usado no
    Meta Ads (ver docstring de `Ga4UtmCampaignDaily` em database/models.py)."""
    client = Ga4Client()
    linhas = client.run_report(
        dimensions=[
            "date", "sessionDefaultChannelGroup", "sessionSource", "sessionMedium",
            "sessionManualCampaignName", "sessionManualAdContent",
        ],
        metrics=["sessions", "activeUsers", "engagedSessions", "keyEvents"],
        start_date=start_date, end_date=end_date,
    )

    # Pre-agrega em Python antes do upsert: `clean_utm` pode fazer duas linhas CRUAS
    # do GA4 colapsarem na MESMA chave (ex: "Campanha X" e "Campanha+X" viram a
    # mesma campanha depois de normalizadas) -- sem essa soma previa, o upsert
    # simplesmente sobrescreveria uma pela outra em vez de somar, subestimando o
    # total real da campanha.
    agregados: dict[tuple, dict] = {}
    for item in linhas:
        dia = parse_ga4_date(item.get("date"))
        if dia is None:
            continue
        chave = (
            dia,
            item.get("sessionDefaultChannelGroup") or "(not set)",
            item.get("sessionSource") or "(not set)",
            item.get("sessionMedium") or "(not set)",
            clean_utm(item.get("sessionManualCampaignName")),
            clean_utm(item.get("sessionManualAdContent")),
        )
        acc = agregados.setdefault(chave, {"sessions": 0, "active_users": 0, "engaged_sessions": 0, "key_events": 0})
        acc["sessions"] += parse_int(item.get("sessions")) or 0
        acc["active_users"] += parse_int(item.get("activeUsers")) or 0
        acc["engaged_sessions"] += parse_int(item.get("engagedSessions")) or 0
        acc["key_events"] += parse_int(item.get("keyEvents")) or 0

    count = 0
    for (dia, canal, origem, midia, campanha, conteudo), valores in agregados.items():
        upsert_by_composite(
            db, Ga4UtmCampaignDaily,
            {
                "date": dia, "channel_group": canal, "source": origem, "medium": midia,
                "utm_campaign": campanha, "utm_content": conteudo,
            },
            valores,
        )
        count += 1
    db.commit()
    return count


def sync_pages(db: Session, start_date: str, end_date: str) -> int:
    """Uma linha por (dia, pagina) -- quais paginas recebem mais trafego."""
    client = Ga4Client()
    linhas = client.run_report(
        dimensions=["date", "pagePath", "pageTitle"],
        metrics=["screenPageViews", "activeUsers"],
        start_date=start_date, end_date=end_date,
    )
    count = 0
    for item in linhas:
        dia = parse_ga4_date(item.get("date"))
        if dia is None:
            continue
        chave = {
            "date": dia,
            "page_path": item.get("pagePath") or "(not set)",
            "page_title": item.get("pageTitle") or "(not set)",
        }
        upsert_by_composite(
            db, Ga4PageDaily, chave,
            {
                "screen_page_views": parse_int(item.get("screenPageViews")),
                "active_users": parse_int(item.get("activeUsers")),
            },
        )
        count += 1
    db.commit()
    return count


def sync_device(db: Session, start_date: str, end_date: str) -> int:
    """Uma linha por (dia, categoria de dispositivo, navegador)."""
    client = Ga4Client()
    linhas = client.run_report(
        dimensions=["date", "deviceCategory", "browser"],
        metrics=["sessions", "activeUsers"],
        start_date=start_date, end_date=end_date,
    )
    count = 0
    for item in linhas:
        dia = parse_ga4_date(item.get("date"))
        if dia is None:
            continue
        chave = {
            "date": dia,
            "device_category": item.get("deviceCategory") or "(not set)",
            "browser": item.get("browser") or "(not set)",
        }
        upsert_by_composite(
            db, Ga4DeviceDaily, chave,
            {
                "sessions": parse_int(item.get("sessions")),
                "active_users": parse_int(item.get("activeUsers")),
            },
        )
        count += 1
    db.commit()
    return count


def sync_geo(db: Session, start_date: str, end_date: str) -> int:
    """Uma linha por (dia, pais, cidade)."""
    client = Ga4Client()
    linhas = client.run_report(
        dimensions=["date", "country", "city"],
        metrics=["sessions", "activeUsers"],
        start_date=start_date, end_date=end_date,
    )
    count = 0
    for item in linhas:
        dia = parse_ga4_date(item.get("date"))
        if dia is None:
            continue
        chave = {
            "date": dia,
            "country": item.get("country") or "(not set)",
            "city": item.get("city") or "(not set)",
        }
        upsert_by_composite(
            db, Ga4GeoDaily, chave,
            {
                "sessions": parse_int(item.get("sessions")),
                "active_users": parse_int(item.get("activeUsers")),
            },
        )
        count += 1
    db.commit()
    return count


def sync_all_reports(db: Session, *, full: bool) -> dict[str, int]:
    """Roda os 5 relatorios pra mesma janela de datas -- usado tanto pela carga
    inicial (full=True, historico inteiro) quanto pela incremental (full=False,
    janela rolante dos ultimos `JANELA_INCREMENTAL_DIAS` dias)."""
    start_date, end_date = _range_full() if full else _range_incremental()
    return {
        "overview": sync_overview(db, start_date, end_date),
        "traffic_source": sync_traffic_source(db, start_date, end_date),
        "utm_campaign": sync_utm_campaign(db, start_date, end_date),
        "pages": sync_pages(db, start_date, end_date),
        "device": sync_device(db, start_date, end_date),
        "geo": sync_geo(db, start_date, end_date),
    }
