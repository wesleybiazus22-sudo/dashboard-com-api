import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # garante que "app"/"database" sejam importaveis

import plotly.graph_objects as go
import streamlit as st

from app.db import query
from app.theme import MV_STATUS_COLORS, STATUS_GOOD, base_layout

st.set_page_config(page_title="Melhor Venda", page_icon="🎯", layout="wide")
st.title("🎯 Melhor Venda — Outbound")
st.caption("Leads prospectados semanalmente, cruzados com o RD Station CRM.")

campaigns = query("select * from v_mv_channel_summary order by week_start")
status_detail = query(
    """
    select campaign_label, week_start, mv_status, count(*) as leads
    from v_mv_campaign_status
    group by campaign_label, week_start, mv_status
    order by week_start
    """
)

if campaigns.empty:
    st.info("Nenhuma campanha carregada ainda. Rode `python -m scripts.load_mv_campaign <arquivo.json>`.")
    st.stop()

# ---------------------------------------------------------------- KPIs (agregado)
total_leads = int(campaigns["leads_total"].sum())
total_conectados = int(campaigns["leads_conectados"].sum())
total_no_crm = int(campaigns["leads_no_crm"].sum())
pct_conexao_geral = round(100 * total_conectados / total_leads, 1) if total_leads else 0
pct_crm_dos_conectados = round(100 * total_no_crm / total_conectados, 1) if total_conectados else 0

col1, col2, col3, col4 = st.columns(4)
col1.metric("Leads prospectados", f"{total_leads}")
col2.metric("Conectados", f"{total_conectados}", f"{pct_conexao_geral}% do total")
col3.metric("Achados no CRM", f"{total_no_crm}")
col4.metric("Conectados → CRM", f"{pct_crm_dos_conectados}%", help="Dos conectados, quantos viraram negociação no CRM")

st.divider()

# ---------------------------------------------------------------- Volume por campanha (empilhado por status)
st.subheader("Volume por campanha")

pivot = status_detail.pivot_table(
    index=["campaign_label", "week_start"], columns="mv_status", values="leads", fill_value=0
).reset_index().sort_values("week_start")

fig_volume = go.Figure()
for status in ["Conectado", "Tentando Contato", "Sem Retorno"]:
    if status in pivot.columns:
        fig_volume.add_trace(
            go.Bar(
                name=status,
                x=pivot["campaign_label"],
                y=pivot[status],
                marker_color=MV_STATUS_COLORS.get(status),
            )
        )
fig_volume.update_layout(barmode="stack")
base_layout(fig_volume, height=380)
st.plotly_chart(fig_volume, use_container_width=True)

# ---------------------------------------------------------------- % conexão ao longo do tempo
st.subheader("Taxa de conexão por campanha")

fig_pct = go.Figure(
    go.Scatter(
        x=campaigns["campaign_label"],
        y=campaigns["pct_conexao"],
        mode="lines+markers+text",
        text=[f"{v}%" for v in campaigns["pct_conexao"]],
        textposition="top center",
        line=dict(color=STATUS_GOOD, width=2),
        marker=dict(size=10, color=STATUS_GOOD),
    )
)
fig_pct.update_yaxes(ticksuffix="%", rangemode="tozero")
base_layout(fig_pct, height=320)
st.plotly_chart(fig_pct, use_container_width=True)

st.divider()

# ---------------------------------------------------------------- Tabela por campanha
st.subheader("Detalhe por campanha")
st.dataframe(
    campaigns.rename(
        columns={
            "campaign_label": "Campanha",
            "sdr_name": "SDR",
            "week_start": "Início",
            "week_end": "Fim",
            "leads_total": "Leads",
            "leads_conectados": "Conectados",
            "pct_conexao": "% Conexão",
            "leads_no_crm": "No CRM",
            "conectados_no_crm": "Conectados no CRM",
        }
    ).drop(columns=["campaign_id"], errors="ignore"),
    use_container_width=True,
    hide_index=True,
)

# ---------------------------------------------------------------- Detalhe por empresa (com filtro)
st.subheader("Detalhe por empresa")
selected_campaign = st.selectbox("Campanha", options=["Todas"] + list(campaigns["campaign_label"]))

detail = query(
    """
    select campaign_label, company_name_mv, mv_status, match_confidence,
           deal_name, canonical_stage, deal_status
    from v_mv_campaign_status
    order by campaign_label desc, company_name_mv
    """
)
if selected_campaign != "Todas":
    detail = detail[detail["campaign_label"] == selected_campaign]

st.dataframe(
    detail.rename(
        columns={
            "campaign_label": "Campanha",
            "company_name_mv": "Empresa (MV)",
            "mv_status": "Status MV",
            "match_confidence": "Confiança",
            "deal_name": "Negociação (CRM)",
            "canonical_stage": "Etapa",
            "deal_status": "Status Negociação",
        }
    ),
    use_container_width=True,
    hide_index=True,
)
