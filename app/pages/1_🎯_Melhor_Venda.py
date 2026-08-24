import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # garante que "app"/"database" sejam importaveis

import plotly.graph_objects as go
import streamlit as st

from app.db import query
from app.theme import CATEGORICAL, MV_STATUS_COLORS, base_layout, inject_brand

st.set_page_config(page_title="Melhor Venda", page_icon="🎯", layout="wide")
inject_brand()

st.image("app/assets/logos/develcode-horizontal-black.png", width=180)

title_col, refresh_col = st.columns([6, 1])
with title_col:
    st.title("🎯 Melhor Venda — Outbound")
    st.caption("Leads prospectados semanalmente, cruzados com o RD Station CRM.")
with refresh_col:
    st.write("")
    if st.button("🔄 Atualizar dados", help="Os dados ficam em cache por 5 min -- clique pra forçar releitura do banco"):
        st.cache_data.clear()
        st.rerun()

campaigns_all = query("select * from v_mv_channel_summary order by week_start")
status_detail_all = query(
    """
    select campaign_id, campaign_label, sdr_name, week_start, mv_status, count(*) as leads
    from v_mv_campaign_status
    group by campaign_id, campaign_label, sdr_name, week_start, mv_status
    order by week_start
    """
)

if campaigns_all.empty:
    st.info("Nenhuma campanha carregada ainda. Rode `python -m scripts.load_mv_campaign <arquivo.json>`.")
    st.stop()


def _to_date(value):
    return value.date() if hasattr(value, "date") else value


# ---------------------------------------------------------------- Filtro de período (slicer)
# As campanhas sao semanais, mas o filtro e por data continua -- uma campanha entra no
# recorte se o periodo escolhido tocar em qualquer dia da semana dela (overlap), o que
# permite recortes mais finos que "semana inteira", como so os ultimos dias de uma
# campanha + comeco da seguinte.
min_date = _to_date(campaigns_all["week_start"].min())
max_date = _to_date(campaigns_all["week_end"].max())

st.subheader("Período")
if min_date == max_date:
    st.caption(f"Única campanha disponível: {min_date.strftime('%d/%m/%Y')}.")
    periodo_inicio, periodo_fim = min_date, max_date
else:
    periodo_inicio, periodo_fim = st.slider(
        "Arraste para filtrar todas as métricas abaixo por período",
        min_value=min_date,
        max_value=max_date,
        value=(min_date, max_date),
        format="DD/MM/YYYY",
        label_visibility="collapsed",
    )

campaigns_periodo = campaigns_all[
    campaigns_all["week_start"].apply(_to_date).le(periodo_fim)
    & campaigns_all["week_end"].apply(_to_date).ge(periodo_inicio)
].copy()

if campaigns_periodo.empty:
    st.warning("Nenhuma campanha no período selecionado.")
    st.stop()

st.caption(
    f"{len(campaigns_periodo)} campanha(s) no período: {periodo_inicio.strftime('%d/%m/%Y')} – {periodo_fim.strftime('%d/%m/%Y')}."
)

# ---------------------------------------------------------------- Filtro de SDR
sdrs_no_periodo = sorted(campaigns_periodo["sdr_name"].dropna().unique())
sdr_selecionado = st.selectbox("SDR", options=["Todas"] + sdrs_no_periodo)

campaigns = campaigns_periodo if sdr_selecionado == "Todas" else campaigns_periodo[campaigns_periodo["sdr_name"] == sdr_selecionado]

if campaigns.empty:
    st.warning("Nenhuma campanha dessa SDR no período selecionado.")
    st.stop()

# campaign_label NAO e chave unica -- duas SDRs podem ter uma campanha com o mesmo
# rotulo de semana (coincidencia de nome, campanhas de verdade diferentes). Todo
# filtro/join abaixo usa campaign_id, a chave real.
campaign_ids_selecionados = set(campaigns["campaign_id"])
status_detail = status_detail_all[status_detail_all["campaign_id"].isin(campaign_ids_selecionados)]

st.divider()

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

# Mais de uma SDR pode ter uma campanha com o mesmo rotulo (ex: "Agosto/Semana 2" da
# Miriã e da Letícia sao campanhas DIFERENTES que so coincidem no nome da semana) --
# sem desambiguar isso, os dois graficos abaixo colidiam no mesmo ponto do eixo X.
label_counts = campaigns.groupby("campaign_label")["sdr_name"].nunique()
labels_duplicados = set(label_counts[label_counts > 1].index)


def _x_label(row) -> str:
    if row["campaign_label"] in labels_duplicados:
        return f"{row['campaign_label']} ({row['sdr_name']})"
    return row["campaign_label"]


# ordem cronologica dos rotulos no eixo X, mesmo com campanhas de SDRs diferentes
# intercaladas
ordem_labels = (
    campaigns.assign(x_label=campaigns.apply(_x_label, axis=1))
    .sort_values("week_start")["x_label"]
    .tolist()
)

# ---------------------------------------------------------------- Volume por campanha (empilhado por status)
st.subheader("Volume por campanha")

status_detail_x = status_detail.copy()
status_detail_x["x_label"] = status_detail_x.apply(_x_label, axis=1)

pivot = status_detail_x.pivot_table(
    index=["x_label", "week_start"], columns="mv_status", values="leads", fill_value=0
).reset_index().sort_values("week_start")

fig_volume = go.Figure()
for status in ["Conectado", "Tentando Contato", "Sem Retorno"]:
    if status in pivot.columns:
        fig_volume.add_trace(
            go.Bar(
                name=status,
                x=pivot["x_label"],
                y=pivot[status],
                marker_color=MV_STATUS_COLORS.get(status),
            )
        )
fig_volume.update_layout(barmode="stack")
fig_volume.update_xaxes(categoryorder="array", categoryarray=ordem_labels)
base_layout(fig_volume, height=380)
st.plotly_chart(fig_volume, use_container_width=True)

# ---------------------------------------------------------------- % conexão ao longo do tempo (uma linha por SDR)
st.subheader("Taxa de conexão por campanha")

campaigns_x = campaigns.assign(x_label=campaigns.apply(_x_label, axis=1))

fig_pct = go.Figure()
for i, sdr in enumerate(sorted(campaigns_x["sdr_name"].dropna().unique())):
    sub = campaigns_x[campaigns_x["sdr_name"] == sdr].sort_values("week_start")
    cor = CATEGORICAL[i % len(CATEGORICAL)]
    fig_pct.add_trace(
        go.Scatter(
            x=sub["x_label"],
            y=sub["pct_conexao"],
            name=sdr,
            mode="lines+markers+text",
            text=[f"{v}%" for v in sub["pct_conexao"]],
            textposition="top center",
            line=dict(color=cor, width=2),
            marker=dict(size=10, color=cor),
        )
    )
fig_pct.update_xaxes(categoryorder="array", categoryarray=ordem_labels)
fig_pct.update_yaxes(ticksuffix="%", rangemode="tozero")
base_layout(fig_pct, height=320)
st.plotly_chart(fig_pct, use_container_width=True)

st.divider()

# ---------------------------------------------------------------- Performance por SDR
# Sempre mostra todas as SDRs do período (ignora o filtro de SDR acima -- esse filtro so
# afeta os KPIs/graficos/tabelas de cima), pra dar visao comparativa.
st.subheader("Performance por SDR")
st.caption("Sempre mostra todas as SDRs do período selecionado, independente do filtro de SDR acima.")

sdr_perf = (
    campaigns_periodo.groupby("sdr_name")
    .agg(
        campanhas=("campaign_label", "nunique"),
        leads=("leads_total", "sum"),
        conectados=("leads_conectados", "sum"),
        no_crm=("leads_no_crm", "sum"),
    )
    .reset_index()
)
sdr_perf["pct_conexao"] = (100 * sdr_perf["conectados"] / sdr_perf["leads"]).round(1)
sdr_perf["pct_crm"] = (100 * sdr_perf["no_crm"] / sdr_perf["conectados"]).round(1)
sdr_perf = sdr_perf.sort_values("leads", ascending=False)

st.dataframe(
    sdr_perf.rename(
        columns={
            "sdr_name": "SDR",
            "campanhas": "Campanhas",
            "leads": "Leads",
            "conectados": "Conectados",
            "pct_conexao": "% Conexão",
            "no_crm": "No CRM",
            "pct_crm": "% Conectados → CRM",
        }
    ),
    use_container_width=True,
    hide_index=True,
)

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
st.caption("Respeita o período e a SDR selecionados acima. Use o campo abaixo pra restringir a uma campanha específica dentro disso.")

# opcoes desambiguadas (rotulo + SDR quando o rotulo colide) mapeadas de volta pro
# campaign_id real, pra nunca misturar campanhas de SDRs diferentes com semana homonima
campanha_opcoes = {"Todas": None}
for _, row in campaigns.sort_values("week_start", ascending=False).iterrows():
    campanha_opcoes[_x_label(row)] = row["campaign_id"]
selected_campaign_label = st.selectbox("Campanha", options=list(campanha_opcoes.keys()))
selected_campaign_id = campanha_opcoes[selected_campaign_label]

detail_all = query(
    """
    select campaign_id, campaign_label, company_name_mv, mv_status, match_confidence,
           deal_name, canonical_stage, deal_status
    from v_mv_campaign_status
    order by campaign_label desc, company_name_mv
    """
)
detail = detail_all[detail_all["campaign_id"].isin(campaign_ids_selecionados)]
if selected_campaign_id is not None:
    detail = detail[detail["campaign_id"] == selected_campaign_id]

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
