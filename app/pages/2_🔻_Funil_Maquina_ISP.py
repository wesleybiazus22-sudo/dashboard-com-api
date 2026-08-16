import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # garante que "app"/"database" sejam importaveis

import plotly.graph_objects as go
import streamlit as st

from app.db import query
from app.theme import (
    CANONICAL_STAGE_LABELS,
    CANONICAL_STAGE_ORDER,
    CAT_BLUE,
    CAT_ORANGE,
    SEQUENTIAL_BLUE,
    base_layout,
    format_duration,
)

st.set_page_config(page_title="Funil Máquina ISP", page_icon="🔻", layout="wide")

title_col, refresh_col = st.columns([6, 1])
with title_col:
    st.title("🔻 Funil Máquina ISP")
    st.caption("Qualificação + Closer tratados como um único funil ponta a ponta.")
with refresh_col:
    st.write("")
    if st.button("🔄 Atualizar dados", help="Os dados ficam em cache por 5 min -- clique pra forçar releitura do banco"):
        st.cache_data.clear()
        st.rerun()

milestones = query("select * from v_maquina_isp_deal_milestones")

if milestones.empty:
    st.info("Sem dados sincronizados ainda pra Máquina ISP.")
    st.stop()

# ---------------------------------------------------------------- Filtros SDR / Closer
st.subheader("Filtros")
filtro_col1, filtro_col2 = st.columns(2)

sdrs = sorted(milestones["sdr_name"].dropna().unique())
closers = sorted(milestones["closer_name"].dropna().unique())

with filtro_col1:
    sdr_selecionado = st.selectbox("SDR (originou a negociação)", options=["Todos"] + sdrs)
with filtro_col2:
    closer_selecionado = st.selectbox("Closer (recebeu a negociação)", options=["Todos"] + closers)

filtrado = milestones.copy()
if sdr_selecionado != "Todos":
    filtrado = filtrado[filtrado["sdr_name"] == sdr_selecionado]
if closer_selecionado != "Todos":
    filtrado = filtrado[filtrado["closer_name"] == closer_selecionado]

if sdr_selecionado != "Todos" or closer_selecionado != "Todos":
    partes = []
    if sdr_selecionado != "Todos":
        partes.append(f"originadas por **{sdr_selecionado}**")
    if closer_selecionado != "Todos":
        partes.append(f"recebidas por **{closer_selecionado}**")
    st.caption(f"Mostrando {filtrado.shape[0]} negociações " + " e ".join(partes) + ".")

st.divider()

# ---------------------------------------------------------------- KPIs de negócio
total_leads = int(filtrado.shape[0])
sdr_ganhos = int(filtrado["sdr_ganhou"].sum())
closer_ganhos = int(filtrado["closer_ganhou"].sum())
taxa_sdr = round(100 * sdr_ganhos / total_leads, 1) if total_leads else 0
taxa_closer = round(100 * closer_ganhos / sdr_ganhos, 1) if sdr_ganhos else 0
abertas = filtrado[filtrado["deal_status"] == "ongoing"]
pipeline_aberto = abertas["amount"].sum()

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Negociações no funil", total_leads)
col2.metric("Ganho SDR", sdr_ganhos, f"{taxa_sdr}% do total", help="Chegou em Reunião Realizada ou além")
col3.metric("Ganho Closer", closer_ganhos, f"{taxa_closer}% dos ganhos SDR", help="Chegou em Freemium")
col4.metric("Pipeline aberto", f"R$ {pipeline_aberto:,.0f}".replace(",", "."))
col5.metric("Negociações abertas", int(abertas.shape[0]))

st.divider()

# ---------------------------------------------------------------- Funil (snapshot atual, respeita o filtro)
st.subheader("Funil — negociações abertas por etapa")

funnel_counts = (
    abertas.groupby("canonical_stage")
    .agg(deals=("deal_id", "count"), pipeline_value=("amount", "sum"))
    .reindex(CANONICAL_STAGE_ORDER)
    .fillna(0)
    .reset_index()
)
funnel_counts["label"] = funnel_counts["canonical_stage"].map(CANONICAL_STAGE_LABELS)

fig_funnel = go.Figure(
    go.Funnel(
        y=funnel_counts["label"],
        x=funnel_counts["deals"],
        textinfo="value+percent initial",
        marker=dict(color=SEQUENTIAL_BLUE[: len(funnel_counts)]),
        connector=dict(line=dict(color="#e1e0d9", width=1)),
    )
)
base_layout(fig_funnel, height=440)
st.plotly_chart(fig_funnel, use_container_width=True)

st.caption(
    "Mostra onde as negociações **abertas agora** estão. Não é cumulativo (uma negociação "
    "que já passou por uma etapa e avançou não conta mais nela)."
)

st.divider()

# ---------------------------------------------------------------- Aging / velocity
st.subheader("Tempo por etapa (velocity)")
if sdr_selecionado != "Todos" or closer_selecionado != "Todos":
    st.caption("⚠️ Este gráfico ainda é sempre da base inteira (não respeita o filtro de SDR/Closer acima).")

velocity = query(
    "select canonical_stage, stage_name, stage_order, media_horas, mediana_horas, parados_agora, "
    "media_horas_parados_agora from v_stage_velocity where product_group = 'Máquina ISP' order by stage_order"
)
# ordem de exibicao: primeira etapa no topo do grafico horizontal
velocity = velocity.sort_values("stage_order", ascending=False)

media_fmt = velocity["media_horas"].apply(format_duration)
parado_fmt = velocity["media_horas_parados_agora"].apply(format_duration)

fig_vel = go.Figure()
fig_vel.add_trace(
    go.Bar(
        name="Média histórica",
        y=velocity["stage_name"],
        x=velocity["media_horas"],
        orientation="h",
        marker_color=CAT_BLUE,
        text=media_fmt,
        textposition="outside",
        customdata=media_fmt,
        hovertemplate="<b>%{y}</b><br>Média histórica: %{customdata}<extra></extra>",
    )
)
fig_vel.add_trace(
    go.Bar(
        name="Parado agora, em média",
        y=velocity["stage_name"],
        x=velocity["media_horas_parados_agora"],
        orientation="h",
        marker_color=CAT_ORANGE,
        text=parado_fmt,
        textposition="outside",
        customdata=parado_fmt,
        hovertemplate="<b>%{y}</b><br>Parado agora, em média: %{customdata}<extra></extra>",
    )
)
fig_vel.update_layout(barmode="group")
fig_vel.update_xaxes(title="horas (rótulo mostra formatado em dias/horas/min)")
base_layout(fig_vel, height=440)
st.plotly_chart(fig_vel, use_container_width=True)

velocity_display = velocity.assign(
    **{
        "Média histórica": media_fmt,
        "Mediana histórica": velocity["mediana_horas"].apply(format_duration),
        "Parados agora": velocity["parados_agora"],
        "Média parado agora": parado_fmt,
    }
)[["stage_name", "Média histórica", "Mediana histórica", "Parados agora", "Média parado agora"]].rename(
    columns={"stage_name": "Etapa"}
)
st.dataframe(velocity_display, use_container_width=True, hide_index=True)

st.divider()

# ---------------------------------------------------------------- Performance SDR
st.subheader("Performance por SDR")
st.caption('"Ganho" aqui = a negociação chegou em Reunião Realizada ou além. Tabela sempre mostra todo mundo.')

sdr_perf = (
    milestones[milestones["sdr_name"].notna()]
    .groupby("sdr_name")
    .agg(leads=("deal_id", "count"), ganhos=("sdr_ganhou", "sum"))
    .reset_index()
)
sdr_perf["taxa_pct"] = (100 * sdr_perf["ganhos"] / sdr_perf["leads"]).round(1)
sdr_perf = sdr_perf.sort_values("ganhos", ascending=False)

st.dataframe(
    sdr_perf.rename(columns={"sdr_name": "SDR", "leads": "Leads", "ganhos": "Ganhos", "taxa_pct": "Taxa %"}),
    use_container_width=True,
    hide_index=True,
)

# ---------------------------------------------------------------- Performance Closer
st.subheader("Performance por Closer")
st.caption('"Ganho" aqui = a negociação chegou em Freemium. Tabela sempre mostra todo mundo.')

closer_base = milestones[milestones["closer_name"].notna()].copy()
closer_base["valor_se_ganho"] = closer_base["amount"].where(closer_base["closer_ganhou"], 0)

closer_perf = (
    closer_base.groupby("closer_name")
    .agg(
        oportunidades=("deal_id", "count"),
        ganhos=("closer_ganhou", "sum"),
        valor_ganho=("valor_se_ganho", "sum"),
    )
    .reset_index()
)
closer_perf["taxa_pct"] = (100 * closer_perf["ganhos"] / closer_perf["oportunidades"]).round(1)
closer_perf = closer_perf.sort_values("ganhos", ascending=False)

st.dataframe(
    closer_perf.rename(
        columns={
            "closer_name": "Closer",
            "oportunidades": "Oportunidades",
            "ganhos": "Ganhos",
            "taxa_pct": "Taxa %",
            "valor_ganho": "Valor Ganho (R$)",
        }
    ),
    use_container_width=True,
    hide_index=True,
)

st.divider()

# ---------------------------------------------------------------- Detalhe das negociações filtradas
st.subheader("Negociações")
detail_cols = ["deal_name", "stage_name", "deal_status", "amount", "sdr_name", "closer_name", "deal_created_at"]
st.dataframe(
    filtrado[detail_cols].rename(
        columns={
            "deal_name": "Negociação",
            "stage_name": "Etapa",
            "deal_status": "Status",
            "amount": "Valor",
            "sdr_name": "SDR",
            "closer_name": "Closer",
            "deal_created_at": "Criada em",
        }
    ),
    use_container_width=True,
    hide_index=True,
)
