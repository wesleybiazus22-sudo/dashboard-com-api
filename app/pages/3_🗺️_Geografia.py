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
    BRAND_INK_200,
    ORIGEM_COLORS,
    REGIAO_COLORS,
    STATUS_CRITICAL,
    STATUS_GOOD,
    UF_NOMES,
    base_layout,
    format_int,
    format_pct,
    inject_brand,
    render_brand_header,
)

PAGE = "geografia"

st.set_page_config(page_title="Geografia", page_icon="🗺️", layout="wide")
inject_brand()

head_col, refresh_col = st.columns([4, 1.3])
with head_col:
    render_brand_header("🗺️ Geografia da operação", "Onde a prospecção acontece, onde ela converte e onde ela trava.")
with refresh_col:
    st.write("")
    st.write("")
    if st.button("🔄 Atualizar", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

deals_all = query("select * from v_maquina_isp_deals_enriched")
if deals_all.empty:
    st.info("Nenhuma negociação no escopo do Máquina ISP.")
    st.stop()

deals_all["deal_created_at"] = pd.to_datetime(deals_all["deal_created_at"], utc=True, errors="coerce")

# ---------------------------------------------------------------- Filtros
with st.container(border=True):
    inicio, fim, _ = filters.filtro_periodo(
        deals_all["deal_created_at"].min(), deals_all["deal_created_at"].max(),
        page=PAGE, label="Período de criação da negociação",
    )
    deals_periodo = filters.recorta_periodo(deals_all, "deal_created_at", inicio, fim)

    g1, g2, g3 = st.columns(3)
    with g1:
        sel_origem = filters.multiselect_dim(deals_periodo, "origem", "Origem", page=PAGE)
    with g2:
        sel_regiao = filters.multiselect_dim(deals_periodo, "regiao", "Região", page=PAGE)
    with g3:
        sel_sdr = filters.multiselect_dim(deals_periodo, "sdr_name", "SDR", page=PAGE)

deals_filtrado = filters.aplica_multiselects(
    deals_periodo, {"origem": sel_origem, "regiao": sel_regiao, "sdr_name": sel_sdr}
)

interact.chips(PAGE)
deals = interact.apply(deals_filtrado, PAGE)
drills = interact.active(PAGE)

if deals.empty:
    st.warning("Nenhuma negociação com essa combinação de filtros.")
    st.stop()

com_uf = deals[deals["uf"].notna()]
cobertura = 100 * len(com_uf) / len(deals) if len(deals) else 0

# Base do mapa, do ranking e da rosca: o recorte dos filtros explicitos, SEM os
# drills de clique. Isso mantem a spec desses graficos identica entre reruns, que e
# requisito pro Streamlit preservar a selecao -- se o mapa fosse alimentado por
# `deals` (ja filtrado), clicar em SP o deixaria com um estado so, o Streamlit
# trataria como widget novo, zeraria a selecao e o filtro se desfaria sozinho.
# O destaque do estado clicado vem da selecao nativa do Plotly, nao de recolorir.
base_geo = deals_filtrado
com_uf_base = base_geo[base_geo["uf"].notna()]

# ---------------------------------------------------------------- KPIs
k1, k2, k3, k4 = st.columns(4)
k1.metric("Negociações no recorte", format_int(len(deals)))
k2.metric("Estados alcançados", format_int(com_uf["uf"].nunique()))
k3.metric("Cidades alcançadas", format_int(deals["cidade"].nunique()))
k4.metric(
    "Com localização", format_pct(cobertura),
    help="Percentual das negociações do recorte com UF identificada. O restante não tem endereço preenchido no CRM.",
)

if cobertura < 100:
    st.caption(
        f"ℹ️ {format_int(len(deals) - len(com_uf))} negociação(ões) sem UF identificada ficam fora do mapa, "
        "mas continuam contadas nos totais das outras páginas."
    )

st.divider()

# ---------------------------------------------------------------- Mapa + ranking
st.subheader("Onde estamos prospectando")

metrica = st.radio(
    "Métrica do mapa",
    ["Volume de negociações", "Taxa de reunião realizada", "Taxa de perda"],
    horizontal=True,
    key=f"metrica::{PAGE}",
)

por_uf = (
    com_uf_base.groupby("uf")
    .agg(
        negociacoes=("deal_id", "nunique"),
        reunioes=("sdr_ganhou", "sum"),
        perdidas=("deal_status", lambda s: int((s == "lost").sum())),
        cidades=("cidade", "nunique"),
    )
    .reset_index()
)
por_uf["taxa_reuniao"] = (100 * por_uf["reunioes"] / por_uf["negociacoes"]).round(1)
por_uf["taxa_perda"] = (100 * por_uf["perdidas"] / por_uf["negociacoes"]).round(1)
por_uf["nome_uf"] = por_uf["uf"].map(UF_NOMES).fillna(por_uf["uf"])

if metrica == "Volume de negociações":
    coluna, rotulo = "negociacoes", "negociações"
elif metrica == "Taxa de reunião realizada":
    coluna, rotulo = "taxa_reuniao", "% reunião realizada"
else:
    coluna, rotulo = "taxa_perda", "% de perda"

m1, m2 = st.columns([3, 2])

with m1:
    fig_mapa = charts.mapa_uf(por_uf, coluna_valor=coluna, titulo_valor=rotulo, altura=520)
    if fig_mapa is None:
        st.warning("Mapa indisponível: arquivo de contornos dos estados não encontrado.")
    else:
        ev = charts.selecionavel(fig_mapa, key=f"{PAGE}_mapa")
        interact.capture(ev, chart_key="mapa", dim="uf", page=PAGE)
        st.caption("Clique em um estado para filtrar a página inteira por ele.")

with m2:
    st.markdown(f"**Ranking por {rotulo}**")
    ranking = por_uf.sort_values(coluna, ascending=False)
    ev = charts.selecionavel(
        charts.barra_dimensao(
            ranking, dimensao="uf", valor=coluna,
            cor_padrao=BRAND_BLUE_600, altura=480, maximo_itens=12,
            sufixo="%" if coluna != "negociacoes" else "",
        ),
        key=f"{PAGE}_ranking",
    )
    interact.capture(ev, chart_key="ranking", dim="uf", page=PAGE)

st.divider()

# ---------------------------------------------------------------- Regiao x origem
st.subheader("Como cada canal se distribui pelo país")
st.caption(
    "Canais diferentes alcançam praças diferentes. Ver isso separado evita confundir "
    "*força de um canal* com *concentração geográfica de quem usa aquele canal*."
)

r1, r2 = st.columns([2, 3])

with r1:
    agg_reg = (
        deals_filtrado.groupby("regiao")["deal_id"].nunique()
        .reset_index(name="negociacoes").sort_values("negociacoes", ascending=False)
    )
    ev = charts.selecionavel(
        charts.rosca(agg_reg, dimensao="regiao", valor="negociacoes", cores=REGIAO_COLORS,
                     centro=f"{format_int(len(deals_filtrado))}<br>negociações", altura=360),
        key=f"{PAGE}_rosca_regiao",
    )
    interact.capture(ev, chart_key="rosca_regiao", dim="regiao", page=PAGE)

with r2:
    cruz = (
        deals.groupby(["regiao", "origem"])["deal_id"].nunique().reset_index(name="negociacoes")
    )
    if cruz.empty:
        st.caption("Sem dados para cruzar região e origem.")
    else:
        ordem_reg = agg_reg["regiao"].tolist()
        fig = go.Figure()
        for origem in sorted(cruz["origem"].unique()):
            sub = cruz[cruz["origem"] == origem].set_index("regiao").reindex(ordem_reg).fillna(0).reset_index()
            fig.add_trace(go.Bar(
                name=origem, x=sub["regiao"], y=sub["negociacoes"],
                marker_color=ORIGEM_COLORS.get(origem, BRAND_INK_200),
                hovertemplate="<b>%{x}</b><br>" + origem + ": %{y}<extra></extra>",
            ))
        fig.update_layout(barmode="stack")
        fig.update_xaxes(showgrid=False)
        base_layout(fig, height=360)
        st.plotly_chart(fig, use_container_width=True, key=f"{PAGE}_regiao_origem")

st.divider()

# ---------------------------------------------------------------- Praças que convertem
st.subheader("Praças que mais convertem")
st.caption(
    "Ordenado por volume, não por taxa: uma UF com 1 negociação e 1 reunião tem 100% de conversão "
    "e não significa nada. A leitura correta é comparar a taxa das praças que já têm volume suficiente."
)

minimo = st.slider(
    "Volume mínimo de negociações para entrar na comparação",
    min_value=1, max_value=max(int(por_uf["negociacoes"].max()), 2),
    value=min(5, max(int(por_uf["negociacoes"].max()), 2)),
    key=f"minvol::{PAGE}",
)

relevantes = por_uf[por_uf["negociacoes"] >= minimo].sort_values("negociacoes", ascending=False)

if relevantes.empty:
    st.info(f"Nenhuma UF tem {minimo} ou mais negociações no recorte atual.")
else:
    # media da mesma base do grafico (sem o filtro de UF), senao a linha de
    # referencia seria a media da UF selecionada comparada contra ela mesma
    media_geral = 100 * base_geo["sdr_ganhou"].sum() / len(base_geo)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=relevantes["uf"], y=relevantes["taxa_reuniao"],
        marker_color=[
            STATUS_GOOD if v >= media_geral else BRAND_BLUE_600 for v in relevantes["taxa_reuniao"]
        ],
        customdata=relevantes[["nome_uf", "negociacoes", "reunioes"]].values,
        text=[f"{v:.0f}%" for v in relevantes["taxa_reuniao"]],
        textposition="outside", cliponaxis=False,
        hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[2]} de %{customdata[1]} chegaram a reunião<br>taxa: %{y}%<extra></extra>",
    ))
    fig.add_hline(
        y=media_geral, line_dash="dot", line_color=STATUS_CRITICAL,
        annotation_text=f"média do recorte: {media_geral:.1f}%", annotation_position="top right",
    )
    fig.update_yaxes(ticksuffix="%", rangemode="tozero")
    fig.update_xaxes(showgrid=False)
    base_layout(fig, height=360)
    fig.update_layout(showlegend=False)
    st.plotly_chart(fig, use_container_width=True, key=f"{PAGE}_convertem")

st.divider()

# ---------------------------------------------------------------- Cidades
st.subheader("Cidades")

cidades = (
    deals[deals["cidade"].notna()]
    .groupby(["cidade", "uf"])
    .agg(
        negociacoes=("deal_id", "nunique"),
        reunioes=("sdr_ganhou", "sum"),
        perdidas=("deal_status", lambda s: int((s == "lost").sum())),
    )
    .reset_index().sort_values("negociacoes", ascending=False)
)
cidades["% reunião"] = (100 * cidades["reunioes"] / cidades["negociacoes"]).round(1)

st.dataframe(
    cidades.rename(columns={
        "cidade": "Cidade", "uf": "UF", "negociacoes": "Negociações",
        "reunioes": "Reuniões realizadas", "perdidas": "Perdidas",
    }),
    use_container_width=True, hide_index=True, height=380,
)

st.download_button(
    "⬇️ Baixar geografia em CSV",
    por_uf.rename(columns={
        "uf": "UF", "nome_uf": "Estado", "negociacoes": "Negociações",
        "reunioes": "Reuniões realizadas", "perdidas": "Perdidas",
        "taxa_reuniao": "% Reunião", "taxa_perda": "% Perda", "cidades": "Cidades",
    }).to_csv(index=False).encode("utf-8-sig"),
    file_name="geografia_maquina_isp.csv",
    mime="text/csv",
)
