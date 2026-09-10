import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # garante que "app"/"database" sejam importaveis

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app import charts, filters
from app.db import query
from app.theme import (
    BRAND_BLUE_600,
    CAT_AQUA,
    CAT_ORANGE,
    base_layout,
    format_int,
    format_money,
    inject_brand,
    render_brand_header,
)

PAGE = "agente"

st.set_page_config(page_title="Agente & Custos", page_icon="🤖", layout="wide")
inject_brand()

head_col, refresh_col = st.columns([4, 1.3])
with head_col:
    render_brand_header(
        "🤖 Agente de Atendimento",
        "Custo de IA (LLM) e mensageria do WhatsApp, e histórico de conversas com os leads.",
    )
with refresh_col:
    st.write("")
    st.write("")
    if st.button("🔄 Atualizar", use_container_width=True, help="Os dados ficam 5 min em cache"):
        st.cache_data.clear()
        st.rerun()

# ---------------------------------------------------------------- Carga
llm_all = query(
    """
    select model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
           custo_usd, finalidade, phone_number, deal_rd_id, occurred_at
    from llm_call_log order by occurred_at
    """
)
custo_wpp_all = query(
    """
    select date, conversation_category, conversation_type, phone_number,
           conversation_count, cost_usd
    from whatsapp_conversation_cost order by date
    """
)
mensagens_all = query(
    """
    select wamid, phone_number, direction, message_type, text_body, contact_name,
           deal_rd_id, occurred_at
    from whatsapp_messages order by occurred_at desc
    """
)

for df, col in ((llm_all, "occurred_at"), (custo_wpp_all, "date"), (mensagens_all, "occurred_at")):
    if not df.empty:
        df[col] = pd.to_datetime(df[col])

sem_dado_nenhum = llm_all.empty and custo_wpp_all.empty and mensagens_all.empty
if sem_dado_nenhum:
    st.info(
        "Ainda não há nenhum dado do agente de atendimento. Assim que o WhatsApp começar a "
        "receber mensagens e o motor de IA entrar em operação, os custos e o histórico de "
        "conversa aparecem aqui automaticamente — não precisa de nenhuma ação manual."
    )

# ---------------------------------------------------------------- KPIs
total_llm = float(llm_all["custo_usd"].sum()) if not llm_all.empty else 0.0
total_wpp = float(custo_wpp_all["cost_usd"].sum()) if not custo_wpp_all.empty else 0.0
total_mensagens = len(mensagens_all)
contatos_unicos = mensagens_all["phone_number"].nunique() if not mensagens_all.empty else 0
total_conversas = int(custo_wpp_all["conversation_count"].sum()) if not custo_wpp_all.empty else 0

k1, k2, k3 = st.columns(3)
k4, k5 = st.columns(2)
k1.metric("Custo total de IA (LLM)", format_money(total_llm, "US$"))
k2.metric("Custo total de mensageria", format_money(total_wpp, "US$"))
k3.metric("Custo combinado do agente", format_money(total_llm + total_wpp, "US$"))
k4.metric("Mensagens trocadas", format_int(total_mensagens))
k5.metric("Contatos únicos", format_int(contatos_unicos), f"{format_int(total_conversas)} conversas cobradas" if total_conversas else None)

st.divider()

# ---------------------------------------------------------------- Custo de LLM
st.subheader("Custo de IA (LLM)")

if llm_all.empty:
    st.caption("Sem chamadas de IA registradas ainda.")
else:
    serie_llm = (
        llm_all.assign(dia=llm_all["occurred_at"].dt.date)
        .groupby("dia")
        .agg(custo=("custo_usd", "sum"), chamadas=("custo_usd", "size"))
        .reset_index()
    )
    c1, c2 = st.columns([3, 2])
    with c1:
        fig = go.Figure(go.Scatter(
            x=serie_llm["dia"], y=serie_llm["custo"], mode="lines+markers",
            line=dict(color=BRAND_BLUE_600, width=2.5, shape="linear"),
            marker=dict(size=6), fill="tozeroy", fillcolor="rgba(0,111,255,0.08)",
            hovertemplate="<b>%{x}</b><br>US$ %{y:.4f}<extra></extra>",
        ))
        fig.update_yaxes(rangemode="tozero", tickprefix="US$ ")
        fig.update_xaxes(showgrid=False)
        base_layout(fig, height=300)
        st.plotly_chart(fig, use_container_width=True, key=f"{PAGE}_llm_serie")

    with c2:
        st.markdown("**Custo por modelo**")
        agg_modelo = llm_all.groupby("model")["custo_usd"].sum().reset_index().sort_values("custo_usd", ascending=False)
        fig_modelo = charts.rosca(
            agg_modelo, dimensao="model", valor="custo_usd",
            centro=f"{format_money(total_llm, 'US$')}", altura=260,
        )
        st.plotly_chart(fig_modelo, use_container_width=True, key=f"{PAGE}_llm_modelo")

    with st.expander(f"Ver chamadas detalhadas ({format_int(len(llm_all))})"):
        cols_llm = {
            "occurred_at": "Quando", "model": "Modelo", "finalidade": "Finalidade",
            "phone_number": "Telefone", "input_tokens": "Tokens entrada", "output_tokens": "Tokens saída",
            "custo_usd": "Custo (US$)",
        }
        tabela_llm = llm_all.sort_values("occurred_at", ascending=False)[list(cols_llm)].rename(columns=cols_llm)
        tabela_llm["Custo (US$)"] = tabela_llm["Custo (US$)"].apply(lambda v: format_money(v, "US$"))
        st.dataframe(tabela_llm.head(200), use_container_width=True, hide_index=True, height=360)

st.divider()

# ---------------------------------------------------------------- Custo de mensageria
st.subheader("Custo de mensageria (WhatsApp)")
st.caption("Valor oficial cobrado pelo Meta por conversa — sincronizado direto da API, não é estimativa nossa.")

if custo_wpp_all.empty:
    st.caption("Sem custo de conversa registrado ainda (normal enquanto o número não tiver conversas reais com lead).")
else:
    serie_wpp = custo_wpp_all.groupby("date").agg(custo=("cost_usd", "sum"), conversas=("conversation_count", "sum")).reset_index()
    c1, c2 = st.columns([3, 2])
    with c1:
        fig = go.Figure(go.Bar(
            x=serie_wpp["date"], y=serie_wpp["custo"], marker=dict(color=CAT_ORANGE),
            hovertemplate="<b>%{x}</b><br>US$ %{y:.4f}<extra></extra>",
        ))
        fig.update_yaxes(rangemode="tozero", tickprefix="US$ ")
        fig.update_xaxes(showgrid=False)
        base_layout(fig, height=300)
        st.plotly_chart(fig, use_container_width=True, key=f"{PAGE}_wpp_serie")
    with c2:
        st.markdown("**Composição por categoria**")
        agg_cat = custo_wpp_all.groupby("conversation_category")["cost_usd"].sum().reset_index().sort_values("cost_usd", ascending=False)
        fig_cat = charts.rosca(
            agg_cat, dimensao="conversation_category", valor="cost_usd",
            centro=f"{format_money(total_wpp, 'US$')}", altura=260,
        )
        st.plotly_chart(fig_cat, use_container_width=True, key=f"{PAGE}_wpp_categoria")

st.divider()

# ---------------------------------------------------------------- Log de conversas
st.subheader("Histórico de conversas")

if mensagens_all.empty:
    st.caption("Nenhuma mensagem trocada ainda.")
else:
    # Rótulo do contato = nome (quando o WhatsApp mandou) + telefone, pra achar
    # a conversa sem decorar número.
    nomes_por_tel = (
        mensagens_all.dropna(subset=["contact_name"])
        .groupby("phone_number")["contact_name"].last()
        .to_dict()
    )
    contatos = sorted(mensagens_all["phone_number"].dropna().unique().tolist())
    rotulo = {t: (f"{nomes_por_tel[t]} · {t}" if t in nomes_por_tel else t) for t in contatos}

    filtro_contato = st.selectbox(
        "Conversa", ["— visão geral (tabela) —"] + contatos,
        format_func=lambda t: t if t.startswith("—") else rotulo.get(t, t),
        key=f"{PAGE}_filtro_contato",
    )

    if filtro_contato.startswith("—"):
        # Visão geral: tabela de tudo, boa pra escanear volume e baixar
        cols_msg = {
            "occurred_at": "Quando", "phone_number": "Telefone", "contact_name": "Nome",
            "direction": "Direção", "message_type": "Tipo", "text_body": "Mensagem",
        }
        tabela_msg = mensagens_all[list(cols_msg)].rename(columns=cols_msg)
        tabela_msg["Direção"] = tabela_msg["Direção"].map(
            {"inbound": "⬅️ Recebida", "outbound": "➡️ Enviada"}
        ).fillna(tabela_msg["Direção"])
        st.caption("Escolha uma conversa acima para ver no formato de chat.")
        st.dataframe(tabela_msg.head(500), use_container_width=True, hide_index=True, height=420)
        st.download_button(
            "⬇️ Baixar histórico em CSV",
            tabela_msg.to_csv(index=False).encode("utf-8-sig"),
            file_name="whatsapp_conversas.csv",
            mime="text/csv",
        )
    else:
        # Conversa única: renderiza como chat de WhatsApp (mais antiga em cima)
        conversa = (
            mensagens_all[mensagens_all["phone_number"] == filtro_contato]
            .sort_values("occurred_at")
        )
        nome = nomes_por_tel.get(filtro_contato)
        st.caption(
            f"**{nome + ' · ' if nome else ''}{filtro_contato}** — "
            f"{format_int(len(conversa))} mensagens · "
            f"{format_int((conversa['direction'] == 'inbound').sum())} do lead, "
            f"{format_int((conversa['direction'] == 'outbound').sum())} do agente"
        )
        # Box de altura fixa com rolagem interna -- a conversa nao estica a
        # pagina toda por mais longa que fique.
        with st.container(height=460, border=True):
            for _, m in conversa.iterrows():
                e_lead = m["direction"] == "inbound"
                with st.chat_message("user" if e_lead else "assistant", avatar="🧑" if e_lead else "🤖"):
                    corpo = m["text_body"] if pd.notna(m["text_body"]) and m["text_body"] else f"_({m['message_type']} — sem texto)_"
                    st.markdown(corpo)
                    st.caption(f"{pd.to_datetime(m['occurred_at']):%d/%m %H:%M}")

st.divider()

# ---------------------------------------------------------------- Base de conhecimento (RAG)
st.subheader("Base de conhecimento do agente")
st.caption(
    "O que o agente consulta pra responder dúvida sobre a solução — extraído dos materiais reais "
    "da empresa. Editar isso é código (ingestion/llm/knowledge_base_seed.py), não é editável aqui."
)

kb_all = query("select titulo, categoria, conteudo, fonte, updated_at from knowledge_chunks order by categoria, titulo")

if kb_all.empty:
    st.caption("Base de conhecimento ainda não carregada — rode `python -m scripts.load_knowledge_base`.")
else:
    st.caption(f"{format_int(len(kb_all))} pedaços carregados, última atualização: {pd.to_datetime(kb_all['updated_at']).max():%d/%m/%Y %H:%M}")
    for categoria, grupo in kb_all.groupby("categoria"):
        with st.expander(f"{categoria} ({len(grupo)})"):
            for _, row in grupo.iterrows():
                st.markdown(f"**{row['titulo']}** _(fonte: {row['fonte'] or '—'})_")
                st.write(row["conteudo"])
                st.markdown("---")

    with st.form(key=f"{PAGE}_teste_busca"):
        pergunta_teste = st.text_input("Testar a busca com uma pergunta de exemplo")
        testar = st.form_submit_button("Buscar")
    if testar and pergunta_teste:
        from database.connection import engine as _engine
        from sqlalchemy.orm import Session as _Session

        from ingestion.llm.retrieval import buscar_contexto

        with _Session(_engine) as _db:
            resultado = buscar_contexto(_db, pergunta_teste, limite=4)
        if not resultado:
            st.warning("Nenhum trecho encontrado pra essa pergunta.")
        else:
            for c in resultado:
                st.markdown(f"**{c.titulo}**")
                st.caption(c.conteudo)
