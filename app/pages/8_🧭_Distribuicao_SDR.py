import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # garante que "app"/"database" sejam importaveis

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.db import query
from app.theme import BRAND_BLUE_600, CAT_ORANGE, base_layout, format_int, format_pct, inject_brand, render_brand_header

st.set_page_config(page_title="Distribuição SDR", page_icon="🧭", layout="wide")
inject_brand()

head_col, refresh_col = st.columns([4, 1.3])
with head_col:
    render_brand_header(
        "Distribuição entre SDRs",
        "Como estão avançando as negociações que estavam em Primeira Conexão e foram divididas entre César e Miriã.",
    )
with refresh_col:
    st.write("")
    st.write("")
    if st.button("🔄 Atualizar", use_container_width=True, help="Os dados ficam 5 min em cache"):
        st.cache_data.clear()
        st.rerun()

# Lista fixa (foto da distribuicao feita em 09/11/2026: so id do card, responsavel antes/depois e motivo) -- o resto vem ao vivo do banco.
ARQUIVO = Path(__file__).resolve().parents[2] / "data" / "distribuicao_sdr_primeira_conexao.csv"
coorte = pd.read_csv(ARQUIVO, sep=";", encoding="utf-8-sig", dtype=str).fillna("")
coorte["transferido"] = coorte["responsavel_atual"].str.split().str[0] != coorte["responsavel_novo"].str.split().str[0]

deals = query(
    """
    select d.rd_id, d.status, s.name as etapa, s."order" as ordem, u.name as dono_agora,
           coalesce(o.name, d.name) as empresa, ct.name as contato,
           nullif(d.raw->'custom_fields'->>'quantidade-de-clientes', '')::numeric as clientes,
           (select max(h.entered_at) from crm_deal_stage_history h
             where h.deal_rd_id = d.rd_id and h.stage_rd_id = d.stage_rd_id) as na_etapa_desde
    from crm_deals d
    left join crm_stages s on s.rd_id = d.stage_rd_id
    left join crm_users u on u.rd_id = d.current_owner_rd_id
    left join crm_organizations o on o.rd_id = d.organization_rd_id
    left join crm_contacts ct on ct.rd_id = d.contact_rd_id
    where d.rd_id = any(:ids)
    """,
    {"ids": coorte["rd_id"].tolist()},
)
df = coorte.merge(deals, on="rd_id", how="left")
df["etapa"] = df["etapa"].fillna("(não encontrada)")
df = df[df["status"].ne("deleted")].copy()  # apagadas no RD (ex: cadastro de teste)
df["sdr"] = df["responsavel_novo"]
df["avancou"] = df["ordem"].fillna(1) >= 2  # Em Prospecção ou além
df["na_etapa_desde"] = pd.to_datetime(df["na_etapa_desde"], utc=True, errors="coerce")

escopo = st.radio(
    "Quais negociações?",
    ["Todas as divididas", "Só as que mudaram de responsável"],
    horizontal=True,
)
if escopo.startswith("Só"):
    df = df[df["transferido"]]

if df.empty:
    st.info("Nenhuma negociação nesse recorte.")
    st.stop()

# ---------------------------------------------------------------- KPIs por SDR
st.subheader("Em prospecção ou além, por SDR")
sdrs = [s for s in ("César", "Miriã") if s in set(df["sdr"])]
cols = st.columns(len(sdrs) + 1)
for col, sdr in zip(cols, sdrs):
    d = df[df["sdr"] == sdr]
    col.metric(sdr, f"{int(d['avancou'].sum())} de {len(d)}", format_pct(100 * d["avancou"].mean() if len(d) else 0))
cols[-1].metric("Total", f"{int(df['avancou'].sum())} de {len(df)}", format_pct(100 * df["avancou"].mean()))
st.caption("\"Avançou\" = saiu de Primeira Conexão (Em Prospecção, Interesse Identificado, Reunião Agendada...). Percentual sobre as negociações do SDR no recorte.")

# ---------------------------------------------------------------- Etapas por SDR
ordem_etapas = (
    df.drop_duplicates("etapa").sort_values("ordem", na_position="last")["etapa"].tolist()
)
pivot = df.pivot_table(index="sdr", columns="etapa", values="rd_id", aggfunc="count", fill_value=0).reindex(columns=ordem_etapas)
fig = go.Figure()
for i, etapa in enumerate(ordem_etapas):
    fig.add_bar(
        name=etapa, x=pivot.index, y=pivot[etapa],
        marker_color=BRAND_BLUE_600 if i == 0 else CAT_ORANGE if i == 1 else None,
        text=pivot[etapa].where(pivot[etapa] > 0), textposition="inside",
    )
fig.update_layout(barmode="stack", yaxis_title="Negociações", legend_title="Etapa atual")
st.plotly_chart(base_layout(fig, 360), use_container_width=True)

tabela_etapas = pivot.copy()
tabela_etapas["Total"] = tabela_etapas.sum(axis=1)
st.dataframe(tabela_etapas, use_container_width=True)

# ---------------------------------------------------------------- Lista
st.subheader("Negociações")
f1, f2 = st.columns(2)
filtro_sdr = f1.multiselect("SDR", sdrs, default=sdrs)
filtro_etapa = f2.multiselect("Etapa", ordem_etapas, default=ordem_etapas)
vis = df[df["sdr"].isin(filtro_sdr) & df["etapa"].isin(filtro_etapa)].sort_values(["sdr", "ordem", "clientes"], ascending=[True, False, False])
st.dataframe(
    vis[["sdr", "empresa", "contato", "clientes", "etapa", "na_etapa_desde", "responsavel_atual", "motivo"]].rename(columns={
        "sdr": "SDR", "empresa": "Empresa", "contato": "Contato", "clientes": "Clientes", "etapa": "Etapa atual",
        "na_etapa_desde": "Na etapa desde", "responsavel_atual": "Responsável antes", "motivo": "Motivo da divisão",
    }),
    use_container_width=True, hide_index=True,
    column_config={"Na etapa desde": st.column_config.DatetimeColumn(format="DD/MM HH:mm"), "Clientes": st.column_config.NumberColumn(format="%d")},
)
st.caption(f"{format_int(len(vis))} negociações. Dados do banco (sincronizado com o RD a cada ~15 min); a lista de quem foi para quem é a foto de 09/11/2026.")
