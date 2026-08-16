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
    STATUS_GOOD,
    base_layout,
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

funnel_df = query(
    "select canonical_stage, deals, pipeline_value from v_funnel_summary where product_group = 'Máquina ISP'"
)
milestones = query("select * from v_maquina_isp_deal_milestones")
velocity = query(
    "select canonical_stage, stage_name, stage_order, media_horas, mediana_horas, parados_agora, "
    "media_horas_parados_agora from v_stage_velocity where product_group = 'Máquina ISP' order by stage_order"
)

if funnel_df.empty and milestones.empty:
    st.info("Sem dados sincronizados ainda pra Máquina ISP.")
    st.stop()

# ---------------------------------------------------------------- KPIs de negócio
total_leads = int(milestones.shape[0])
sdr_ganhos = int(milestones["sdr_ganhou"].sum()) if not milestones.empty else 0
closer_ganhos = int(milestones["closer_ganhou"].sum()) if not milestones.empty else 0
taxa_sdr = round(100 * sdr_ganhos / total_leads, 1) if total_leads else 0
taxa_closer = round(100 * closer_ganhos / sdr_ganhos, 1) if sdr_ganhos else 0
pipeline_aberto = funnel_df["pipeline_value"].sum() if not funnel_df.empty else 0

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Negociações no funil", total_leads)
col2.metric("Ganho SDR", sdr_ganhos, f"{taxa_sdr}% do total", help="Chegou em Reunião Realizada ou além")
col3.metric("Ganho Closer", closer_ganhos, f"{taxa_closer}% dos ganhos SDR", help="Chegou em Freemium")
col4.metric("Pipeline aberto", f"R$ {pipeline_aberto:,.0f}".replace(",", "."))
col5.metric("Negociações abertas", int(funnel_df["deals"].sum()) if not funnel_df.empty else 0)

st.divider()

# ---------------------------------------------------------------- Funil (snapshot atual)
st.subheader("Funil — negociações abertas por etapa")

funnel_df = funnel_df.set_index("canonical_stage").reindex(CANONICAL_STAGE_ORDER).fillna(0).reset_index()
funnel_df["label"] = funnel_df["canonical_stage"].map(CANONICAL_STAGE_LABELS)

fig_funnel = go.Figure(
    go.Funnel(
        y=funnel_df["label"],
        x=funnel_df["deals"],
        textinfo="value+percent initial",
        marker=dict(color=SEQUENTIAL_BLUE[: len(funnel_df)]),
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

fig_vel = go.Figure()
fig_vel.add_trace(
    go.Bar(
        name="Média histórica (dias)",
        x=velocity["stage_name"],
        y=(velocity["media_horas"] / 24).round(1),
        marker_color=CAT_BLUE,
    )
)
fig_vel.add_trace(
    go.Bar(
        name="Parado agora, em média (dias)",
        x=velocity["stage_name"],
        y=(velocity["media_horas_parados_agora"] / 24).round(1),
        marker_color=CAT_ORANGE,
    )
)
fig_vel.update_layout(barmode="group")
fig_vel.update_yaxes(title="dias")
base_layout(fig_vel, height=380)
st.plotly_chart(fig_vel, use_container_width=True)

velocity_display = velocity.rename(
    columns={
        "stage_name": "Etapa",
        "media_horas": "Média histórica (h)",
        "mediana_horas": "Mediana histórica (h)",
        "parados_agora": "Parados agora",
        "media_horas_parados_agora": "Média parado agora (h)",
    }
).drop(columns=["canonical_stage", "stage_order"])
st.dataframe(velocity_display, use_container_width=True, hide_index=True)

st.divider()

# ---------------------------------------------------------------- Performance SDR
st.subheader("Performance por SDR")
st.caption('"Ganho" aqui = a negociação chegou em Reunião Realizada ou além.')

sdr_perf = query(
    """
    select sdr_name, count(*) as leads,
           count(*) filter (where sdr_ganhou) as ganhos,
           round(100.0 * count(*) filter (where sdr_ganhou) / nullif(count(*), 0), 1) as taxa_pct
    from v_maquina_isp_deal_milestones
    where sdr_name is not null
    group by sdr_name
    order by ganhos desc
    """
)
st.dataframe(
    sdr_perf.rename(columns={"sdr_name": "SDR", "leads": "Leads", "ganhos": "Ganhos", "taxa_pct": "Taxa %"}),
    use_container_width=True,
    hide_index=True,
)

# ---------------------------------------------------------------- Performance Closer
st.subheader("Performance por Closer")
st.caption('"Ganho" aqui = a negociação chegou em Freemium.')

closer_perf = query(
    """
    select closer_name, count(*) as oportunidades,
           count(*) filter (where closer_ganhou) as ganhos,
           round(100.0 * count(*) filter (where closer_ganhou) / nullif(count(*), 0), 1) as taxa_pct,
           coalesce(sum(amount) filter (where closer_ganhou), 0) as valor_ganho
    from v_maquina_isp_deal_milestones
    where closer_name is not null
    group by closer_name
    order by ganhos desc
    """
)
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
