import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # garante que "app"/"database" sejam importaveis

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app import charts, filters, interact
from app.db import query
from app.theme import (
    BRAND_BLUE_600,
    CAT_AQUA,
    CAT_ORANGE,
    CAT_VIOLET,
    base_layout,
    format_int,
    format_pct,
    inject_brand,
    render_brand_header,
)

PAGE = "ga4"

st.set_page_config(page_title="Google Analytics 4", page_icon="📈", layout="wide")
inject_brand()

head_col, refresh_col = st.columns([4, 1.3])
with head_col:
    render_brand_header("📈 Google Analytics 4", "Tráfego e comportamento no site — sessões, origem, páginas e dispositivos.")
with refresh_col:
    st.write("")
    st.write("")
    if st.button("🔄 Atualizar", use_container_width=True, help="Os dados ficam 5 min em cache"):
        st.cache_data.clear()
        st.rerun()


def _fmt_duracao(segundos) -> str:
    """Formata segundos em "Xmin Ys" -- duracao de sessao normalmente fica na
    escala de minutos, entao nao reaproveita `format_duration` do theme.py (essa
    é calibrada pra durações em dias/horas das etapas do funil de CRM)."""
    if segundos is None or segundos != segundos:
        return "—"
    m, s = divmod(int(segundos), 60)
    return f"{m}min {s}s" if m else f"{s}s"


# ---------------------------------------------------------------- Carga
overview_all = query("select * from ga4_daily_overview order by date")
traffic_all = query("select * from ga4_traffic_source_daily order by date")
utm_all = query("select * from ga4_utm_campaign_daily order by date")
pages_all = query("select * from ga4_page_daily order by date")
device_all = query("select * from ga4_device_daily order by date")
geo_all = query("select * from ga4_geo_daily order by date")

if overview_all.empty:
    st.info(
        "Nenhum dado do GA4 sincronizado ainda. Configure as credenciais "
        "(GA4_PROPERTY_ID e GA4_SERVICE_ACCOUNT_JSON no .env) e rode `python -m ingestion.sync_all full`."
    )
    st.stop()

for df in (overview_all, traffic_all, utm_all, pages_all, device_all, geo_all):
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])

# ---------------------------------------------------------------- Filtros
with st.container(border=True):
    inicio, fim, _preset = filters.filtro_periodo(
        overview_all["date"].min(), overview_all["date"].max(), page=PAGE, label="Período",
        default="Desde agosto",
    )
    _gran_label, gran_regra = filters.filtro_granularidade(PAGE, default="Dia")

overview_periodo = filters.recorta_periodo(overview_all, "date", inicio, fim)
traffic_periodo = filters.recorta_periodo(traffic_all, "date", inicio, fim)
utm_periodo = filters.recorta_periodo(utm_all, "date", inicio, fim)
pages_periodo = filters.recorta_periodo(pages_all, "date", inicio, fim)
device_periodo = filters.recorta_periodo(device_all, "date", inicio, fim)
geo_periodo = filters.recorta_periodo(geo_all, "date", inicio, fim)

interact.chips(PAGE)

if overview_periodo.empty:
    st.warning("Nenhum dado no período selecionado.")
    st.stop()

# ---------------------------------------------------------------- KPIs
total_sessions = int(overview_periodo["sessions"].sum())
total_users = int(overview_periodo["active_users"].sum())
total_new_users = int(overview_periodo["new_users"].sum())
total_pageviews = int(overview_periodo["screen_page_views"].sum())
total_key_events = int(overview_periodo["key_events"].sum())

# Medias ponderadas por sessao no periodo -- media simples das taxas diarias
# distorceria dias de pouco trafego (ex: 1 sessao com 100% de engajamento pesando
# igual a um dia com 500 sessoes).
if total_sessions:
    engagement_rate = (overview_periodo["engagement_rate"] * overview_periodo["sessions"]).sum() / total_sessions
    avg_duration = (overview_periodo["avg_session_duration"] * overview_periodo["sessions"]).sum() / total_sessions
else:
    engagement_rate = None
    avg_duration = None

k1, k2, k3 = st.columns(3)
k4, k5, k6 = st.columns(3)
k1.metric("Sessões", format_int(total_sessions))
k2.metric("Usuários ativos", format_int(total_users))
k3.metric("Novos usuários", format_int(total_new_users))
k4.metric("Taxa de engajamento", format_pct(100 * engagement_rate if engagement_rate is not None else None, 1))
k5.metric("Duração média de sessão", _fmt_duracao(avg_duration))
k6.metric(
    "Visualizações de página", format_int(total_pageviews),
    f"{format_int(total_key_events)} eventos-chave" if total_key_events else None,
    help="Eventos-chave = 'key events' do GA4 (antigo nome: conversões) -- depende de eventos "
    "marcados como chave na propriedade. Zero aqui normalmente significa que nenhum evento foi "
    "marcado como chave ainda, não que o site não converte.",
)

st.divider()

# ---------------------------------------------------------------- Evolucao temporal
st.subheader("Sessões e usuários no tempo")

periodo_pandas = {"D": "D", "W-MON": "W-MON", "MS": "M"}[gran_regra]
serie = overview_periodo.copy()
serie["bucket"] = serie["date"].dt.to_period(periodo_pandas).dt.start_time
evol = serie.groupby("bucket").agg(
    sessions=("sessions", "sum"), active_users=("active_users", "sum"), screen_page_views=("screen_page_views", "sum"),
).reset_index()

fig_evol = go.Figure()
fig_evol.add_trace(go.Scatter(
    x=evol["bucket"], y=evol["sessions"], name="Sessões", mode="lines+markers",
    line=dict(color=BRAND_BLUE_600, width=2.5, shape="spline", smoothing=0.5),
    marker=dict(size=6), fill="tozeroy", fillcolor="rgba(0,87,234,0.08)",
    hovertemplate="<b>%{x|%d/%m/%Y}</b><br>%{y} sessões<extra></extra>",
))
fig_evol.add_trace(go.Scatter(
    x=evol["bucket"], y=evol["active_users"], name="Usuários ativos", mode="lines+markers",
    line=dict(color=CAT_ORANGE, width=2, dash="dot"), marker=dict(size=6),
    hovertemplate="<b>%{x|%d/%m/%Y}</b><br>%{y} usuários<extra></extra>",
))
fig_evol.update_xaxes(showgrid=False)
base_layout(fig_evol, height=360)
st.plotly_chart(fig_evol, use_container_width=True, key=f"{PAGE}_evolucao")

st.divider()

# ---------------------------------------------------------------- Origem de trafego
st.subheader("Origem do tráfego")
st.caption("Clique num canal para detalhar a origem/mídia exatas abaixo.")

traffic_drill = interact.apply(traffic_periodo, PAGE)

c1, c2 = st.columns([3, 2])

agg_canal = (
    traffic_periodo.groupby("channel_group")
    .agg(sessions=("sessions", "sum"), active_users=("active_users", "sum"), key_events=("key_events", "sum"))
    .reset_index()
    .sort_values("sessions", ascending=False)
)

with c1:
    if agg_canal.empty:
        st.caption("Sem dados de origem no período.")
    else:
        ev = charts.selecionavel(
            charts.barra_dimensao(
                agg_canal, dimensao="channel_group", valor="sessions",
                cor_padrao=CAT_AQUA, altura=340, maximo_itens=12,
            ),
            key=f"{PAGE}_canal",
        )
        interact.capture(ev, chart_key="canal", dim="channel_group", page=PAGE)

with c2:
    st.markdown("**Composição por canal**")
    if agg_canal.empty:
        st.caption("Sem dados no período.")
    else:
        fig_rosca = charts.rosca(
            agg_canal, dimensao="channel_group", valor="sessions",
            centro=f"{format_int(int(agg_canal['sessions'].sum()))}<br>sessões", altura=300,
        )
        st.plotly_chart(fig_rosca, use_container_width=True, key=f"{PAGE}_canal_rosca")

agg_fonte = (
    traffic_drill.groupby(["channel_group", "source", "medium"])
    .agg(sessions=("sessions", "sum"), active_users=("active_users", "sum"), key_events=("key_events", "sum"))
    .reset_index()
    .sort_values("sessions", ascending=False)
)
with st.expander(f"Ver origem/mídia detalhada ({format_int(len(agg_fonte))} combinações)"):
    cols_fonte = {
        "channel_group": "Canal", "source": "Origem", "medium": "Mídia",
        "sessions": "Sessões", "active_users": "Usuários ativos", "key_events": "Eventos-chave",
    }
    st.dataframe(
        agg_fonte[list(cols_fonte)].rename(columns=cols_fonte).head(50),
        use_container_width=True, hide_index=True, height=360,
    )

st.divider()

# ---------------------------------------------------------------- Campanhas (UTM)
st.subheader("Campanhas (UTM)")
st.caption(
    "Quebra pela tag `utm_campaign` que você mesmo coloca no link do anúncio -- casa com o nome "
    "exato usado no Meta Ads, mais preciso que a detecção automática de canal acima. "
    "**(not set)** = tráfego sem UTM (direto, orgânico, referência). Clique numa campanha para "
    "detalhar o `utm_content` (criativo) abaixo."
)

agg_utm_campanha = (
    utm_periodo.groupby("utm_campaign")
    .agg(sessions=("sessions", "sum"), active_users=("active_users", "sum"), key_events=("key_events", "sum"))
    .reset_index()
    .sort_values("sessions", ascending=False)
)

if agg_utm_campanha.empty:
    st.caption("Sem dados de campanha no período.")
else:
    ev = charts.selecionavel(
        charts.barra_dimensao(
            agg_utm_campanha, dimensao="utm_campaign", valor="sessions",
            cor_padrao=CAT_VIOLET, altura=max(280, 26 * min(len(agg_utm_campanha), 12)), maximo_itens=12,
        ),
        key=f"{PAGE}_utm_campanha",
    )
    interact.capture(ev, chart_key="utm_campanha", dim="utm_campaign", page=PAGE)

    utm_drill = interact.apply(utm_periodo, PAGE)
    agg_utm_conteudo = (
        utm_drill.groupby(["utm_campaign", "utm_content"])
        .agg(sessions=("sessions", "sum"), active_users=("active_users", "sum"), key_events=("key_events", "sum"))
        .reset_index()
        .sort_values("sessions", ascending=False)
    )
    with st.expander(f"Ver criativo (utm_content) detalhado ({format_int(len(agg_utm_conteudo))} combinações)"):
        cols_utm = {
            "utm_campaign": "Campanha (UTM)", "utm_content": "Criativo (UTM)",
            "sessions": "Sessões", "active_users": "Usuários ativos", "key_events": "Eventos-chave",
        }
        st.dataframe(
            agg_utm_conteudo[list(cols_utm)].rename(columns=cols_utm).head(50),
            use_container_width=True, hide_index=True, height=360,
        )

        st.download_button(
            "⬇️ Baixar campanhas UTM em CSV",
            agg_utm_conteudo.rename(columns=cols_utm).to_csv(index=False).encode("utf-8-sig"),
            file_name="ga4_campanhas_utm.csv",
            mime="text/csv",
        )

st.divider()

# ---------------------------------------------------------------- Paginas mais visitadas
st.subheader("Páginas mais visitadas")

agg_pagina = (
    pages_periodo.groupby(["page_path", "page_title"])
    .agg(screen_page_views=("screen_page_views", "sum"), active_users=("active_users", "sum"))
    .reset_index()
    .sort_values("screen_page_views", ascending=False)
)

if agg_pagina.empty:
    st.caption("Sem dados de página no período.")
else:
    fig_paginas = charts.barra_dimensao(
        agg_pagina, dimensao="page_path", valor="screen_page_views",
        cor_padrao=CAT_VIOLET, altura=max(280, 26 * min(len(agg_pagina), 12)), maximo_itens=12,
    )
    st.plotly_chart(fig_paginas, use_container_width=True, key=f"{PAGE}_paginas")

    with st.expander(f"Ver tabela completa ({format_int(len(agg_pagina))} páginas)"):
        cols_pagina = {"page_path": "Caminho", "page_title": "Título", "screen_page_views": "Visualizações", "active_users": "Usuários ativos"}
        st.dataframe(
            agg_pagina[list(cols_pagina)].rename(columns=cols_pagina),
            use_container_width=True, hide_index=True, height=360,
        )

st.divider()

# ---------------------------------------------------------------- Dispositivo
st.subheader("Dispositivo e navegador")

d1, d2 = st.columns(2)

agg_dispositivo = (
    device_periodo.groupby("device_category")["sessions"].sum().reset_index().sort_values("sessions", ascending=False)
)
with d1:
    st.markdown("**Categoria de dispositivo**")
    if agg_dispositivo.empty:
        st.caption("Sem dados no período.")
    else:
        fig_disp = charts.rosca(
            agg_dispositivo, dimensao="device_category", valor="sessions",
            centro=f"{format_int(int(agg_dispositivo['sessions'].sum()))}<br>sessões", altura=300,
        )
        st.plotly_chart(fig_disp, use_container_width=True, key=f"{PAGE}_dispositivo")

agg_navegador = (
    device_periodo.groupby("browser")["sessions"].sum().reset_index().sort_values("sessions", ascending=False)
)
with d2:
    st.markdown("**Navegador**")
    if agg_navegador.empty:
        st.caption("Sem dados no período.")
    else:
        fig_nav = charts.barra_dimensao(
            agg_navegador, dimensao="browser", valor="sessions",
            cor_padrao=CAT_ORANGE, altura=300, maximo_itens=8,
        )
        st.plotly_chart(fig_nav, use_container_width=True, key=f"{PAGE}_navegador")

st.divider()

# ---------------------------------------------------------------- Geografia
st.subheader("Geografia")
st.caption("Clique num país para ver as cidades de origem do tráfego abaixo.")

agg_pais = (
    geo_periodo.groupby("country")["sessions"].sum().reset_index().sort_values("sessions", ascending=False)
)

if agg_pais.empty:
    st.caption("Sem dados geográficos no período.")
else:
    ev = charts.selecionavel(
        charts.barra_dimensao(
            agg_pais, dimensao="country", valor="sessions",
            cor_padrao=BRAND_BLUE_600, altura=320, maximo_itens=10,
        ),
        key=f"{PAGE}_pais",
    )
    interact.capture(ev, chart_key="pais", dim="country", page=PAGE)

    geo_drill = interact.apply(geo_periodo, PAGE)
    agg_cidade = (
        geo_drill.groupby(["country", "city"])["sessions"].sum().reset_index().sort_values("sessions", ascending=False)
    )
    with st.expander(f"Ver cidades ({format_int(len(agg_cidade))})"):
        cols_cidade = {"country": "País", "city": "Cidade", "sessions": "Sessões"}
        st.dataframe(
            agg_cidade[list(cols_cidade)].rename(columns=cols_cidade).head(50),
            use_container_width=True, hide_index=True, height=360,
        )

st.divider()
st.caption(
    "ℹ️ Ainda não há cruzamento com o funil do RD CRM (ex: quantas reuniões vieram de qual página/origem) -- "
    "nenhuma negociação carrega hoje o Client ID ou UTM de origem do GA4."
)
