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
    CAT_ORANGE,
    DEAL_STATUS_LABELS,
    ORIGEM_COLORS,
    REGIAO_COLORS,
    STATUS_CRITICAL,
    STATUS_GOOD,
    base_layout,
    format_days,
    format_duration,
    format_int,
    format_pct,
    inject_brand,
    render_brand_header,
)

PAGE = "funil_isp"

st.set_page_config(page_title="Funil Máquina ISP", page_icon="🔻", layout="wide")
inject_brand()

head_col, refresh_col = st.columns([6, 1])
with head_col:
    render_brand_header("🔻 Funil Máquina ISP", "Da prospecção ao Freemium — volume, conversão, velocidade e geografia.")
with refresh_col:
    st.write("")
    st.write("")
    if st.button("🔄 Atualizar", use_container_width=True, help="Os dados ficam 5 min em cache"):
        st.cache_data.clear()
        st.rerun()

# ---------------------------------------------------------------- Carga
deals_all = query("select * from v_maquina_isp_deals_enriched")
if deals_all.empty:
    st.info("Nenhuma negociação no escopo do Máquina ISP.")
    st.stop()

deals_all["deal_created_at"] = pd.to_datetime(deals_all["deal_created_at"], utc=True, errors="coerce")
deals_all["deal_status_label"] = deals_all["deal_status"].map(DEAL_STATUS_LABELS).fillna(deals_all["deal_status"])

alcance_all = query("select * from v_maquina_isp_stage_reach")

# ---------------------------------------------------------------- Filtros
with st.container(border=True):
    c_data, c_gran = st.columns([3, 1])
    with c_data:
        inicio, fim, _preset = filters.filtro_periodo(
            deals_all["deal_created_at"].min(),
            deals_all["deal_created_at"].max(),
            page=PAGE,
            label="Período de criação da negociação",
        )
    with c_gran:
        _gran_label, gran_regra = filters.filtro_granularidade(PAGE)

    deals_periodo = filters.recorta_periodo(deals_all, "deal_created_at", inicio, fim)

    f1, f2, f3, f4, f5 = st.columns(5)
    with f1:
        sel_origem = filters.multiselect_dim(deals_periodo, "origem", "Origem", page=PAGE)
    with f2:
        sel_regiao = filters.multiselect_dim(deals_periodo, "regiao", "Região", page=PAGE)
    with f3:
        sel_uf = filters.multiselect_dim(deals_periodo, "uf", "UF", page=PAGE)
    with f4:
        sel_sdr = filters.multiselect_dim(deals_periodo, "sdr_name", "SDR", page=PAGE)
    with f5:
        sel_status = filters.multiselect_dim(deals_periodo, "deal_status_label", "Status", page=PAGE)

deals_filtrado = filters.aplica_multiselects(
    deals_periodo,
    {
        "origem": sel_origem,
        "regiao": sel_regiao,
        "uf": sel_uf,
        "sdr_name": sel_sdr,
        "deal_status_label": sel_status,
    },
)

# drill (clique nos graficos) por cima dos filtros explicitos
interact.chips(PAGE)
drills = interact.active(PAGE)
deals = interact.apply(deals_filtrado, PAGE)

if deals.empty:
    st.warning("Nenhuma negociação com essa combinação de filtros. Remova algum filtro acima.")
    st.stop()

st.caption(
    f"**{format_int(len(deals))}** negociações no recorte atual "
    f"(de {format_int(len(deals_all))} no escopo total do produto)."
)

# ---------------------------------------------------------------- KPIs
total = len(deals)
sdr_ganhos = int(deals["sdr_ganhou"].sum())
closer_ganhos = int(deals["closer_ganhou"].sum())
# Quantas ESTAO em Freemium agora, vs quantas ja CHEGARAM la em algum momento --
# sao numeros diferentes de proposito (ver "closer_ganhou" em
# v_maquina_isp_deal_milestones): uma negociacao que chegou em Freemium e depois
# foi movida pra "Desistiu" continua contando como conquista do closer (o
# trabalho dele foi feito), mas nao esta mais ativa em Freemium hoje. Mostrar so
# o numero cumulativo sem essa distincao lia como se todas ainda estivessem la.
freemium_ativos = int((deals["stage_name"] == "Freemium").sum())
perdidas = int((deals["deal_status"] == "lost").sum())
andamento = int((deals["deal_status"] == "ongoing").sum())
mediana_dias = deals["dias_no_funil"].median()

k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Negociações", format_int(total))
k2.metric("Em andamento", format_int(andamento), f"{format_pct(100 * andamento / total)} do total")
k3.metric(
    "Reunião realizada", format_int(sdr_ganhos),
    f"{format_pct(100 * sdr_ganhos / total)} de conversão",
    help="Entrega da SDR: a negociação alcançou 'Reunião Realizada' ou além, independente do desfecho final.",
)
k4.metric(
    "Freemium (ativos agora)", format_int(freemium_ativos),
    f"{format_int(closer_ganhos)} chegaram lá ao todo",
    help=(
        "Negociações que estão NA ETAPA Freemium neste exato momento. "
        f"No total, {format_int(closer_ganhos)} já chegaram a Freemium alguma vez -- "
        "esse número maior inclui as que saíram depois (ex: para 'Desistiu') e "
        "continua contando como fechamento de verdade do closer, mesmo que a "
        "negociação não esteja mais ativa lá hoje."
    ),
)
k5.metric("Perdidas", format_int(perdidas), f"{format_pct(100 * perdidas / total)} do total", delta_color="inverse")
k6.metric("Tempo mediano no funil", format_duration((mediana_dias or 0) * 24))

st.divider()

# ---------------------------------------------------------------- Funil
st.subheader("Funil de conversão")
st.caption(
    "Cada barra é quantas negociações **alcançaram** aquela etapa (por histórico ou por estarem nela/adiante) — "
    "não quantas estão paradas nela hoje. É o que faz o funil ser sempre decrescente e, portanto, interpretável."
)

alcance = alcance_all[alcance_all["deal_id"].isin(deals["deal_id"])]
if alcance.empty:
    st.info("Sem histórico de etapas para as negociações filtradas.")
else:
    funil_df = (
        alcance.groupby(["passo", "etapa"])["deal_id"].nunique()
        .reset_index(name="negociacoes").sort_values("passo").reset_index(drop=True)
    )
    topo = funil_df["negociacoes"].max()
    funil_df["anterior"] = funil_df["negociacoes"].shift(1)
    funil_df["conversao_etapa_pct"] = (100 * funil_df["negociacoes"] / funil_df["anterior"]).round(1)
    funil_df["retencao_topo_pct"] = (100 * funil_df["negociacoes"] / topo).round(1)
    funil_df["perdidas_no_passo"] = (
        funil_df["anterior"].fillna(funil_df["negociacoes"]) - funil_df["negociacoes"]
    ).astype(int)

    fc1, fc2 = st.columns([3, 2])
    with fc1:
        # NAO clicavel de proposito: o funil e o grafico que mais precisa REAGIR aos
        # drills (ver o funil so de Melhor Venda, so do Sul, etc). Um grafico clicavel
        # precisa manter a spec estavel entre reruns pro Streamlit nao descartar a
        # selecao -- as duas coisas sao incompativeis, e reagir vale mais aqui.
        st.plotly_chart(charts.funil(funil_df), use_container_width=True, key=f"{PAGE}_funil")
    with fc2:
        st.markdown("**Onde o funil vaza** — negociações perdidas em cada passagem")
        fig_queda = charts.queda_entre_etapas(funil_df)
        if fig_queda is not None:
            st.plotly_chart(fig_queda, use_container_width=True, key=f"{PAGE}_queda")
        else:
            st.caption("Nenhuma queda entre etapas no recorte atual.")

    # leitura automatica do maior gargalo -- o insight que alguem procuraria na mao
    gargalo = funil_df[funil_df["conversao_etapa_pct"].notna()].nsmallest(1, "conversao_etapa_pct")
    if not gargalo.empty:
        g = gargalo.iloc[0]
        pos = int(g.name)
        anterior = funil_df.iloc[pos - 1]["etapa"] if pos > 0 else "topo do funil"
        st.info(
            f"🔎 **Maior gargalo:** a passagem de **{anterior}** para **{g['etapa']}** converte apenas "
            f"**{format_pct(g['conversao_etapa_pct'])}** — {format_int(g['perdidas_no_passo'])} negociações param aí. "
            f"É o ponto de maior retorno para qualquer melhoria de processo."
        )

st.divider()

# ---------------------------------------------------------------- Dimensoes clicaveis
st.subheader("Composição da carteira")
st.caption("Clique em qualquer item para filtrar a página inteira por ele.")

d1, d2, d3 = st.columns(3)

with d1:
    st.markdown("**Por origem**")
    agg = deals_filtrado.groupby("origem")["deal_id"].nunique().reset_index(name="negociacoes").sort_values("negociacoes", ascending=False)
    ev = charts.selecionavel(
        charts.barra_dimensao(agg, dimensao="origem", valor="negociacoes",
                              cores=ORIGEM_COLORS, altura=300),
        key=f"{PAGE}_origem",
    )
    interact.capture(ev, chart_key="origem", dim="origem", page=PAGE)

with d2:
    st.markdown("**Por região**")
    agg = deals_filtrado.groupby("regiao")["deal_id"].nunique().reset_index(name="negociacoes").sort_values("negociacoes", ascending=False)
    ev = charts.selecionavel(
        charts.barra_dimensao(agg, dimensao="regiao", valor="negociacoes",
                              cores=REGIAO_COLORS, altura=300),
        key=f"{PAGE}_regiao",
    )
    interact.capture(ev, chart_key="regiao", dim="regiao", page=PAGE)

with d3:
    st.markdown("**Por SDR**")
    agg = deals_filtrado.groupby("sdr_name")["deal_id"].nunique().reset_index(name="negociacoes").sort_values("negociacoes", ascending=False)
    ev = charts.selecionavel(
        charts.barra_dimensao(agg, dimensao="sdr_name", valor="negociacoes",
                              altura=300),
        key=f"{PAGE}_sdr",
    )
    interact.capture(ev, chart_key="sdr", dim="sdr_name", page=PAGE)

st.divider()

# ---------------------------------------------------------------- Evolucao temporal
st.subheader("Evolução no tempo")

serie = deals.copy()
periodo_pandas = {"D": "D", "W-MON": "W-MON", "MS": "M"}[gran_regra]
serie["bucket"] = serie["deal_created_at"].dt.tz_convert(None).dt.to_period(periodo_pandas).dt.start_time

evol = serie.groupby("bucket").agg(
    Criadas=("deal_id", "nunique"),
    Perdidas=("deal_status", lambda s: int((s == "lost").sum())),
).reset_index()
ganhos = serie[serie["sdr_ganhou"]].groupby("bucket")["deal_id"].nunique().reset_index(name="Reunião realizada")
evol = evol.merge(ganhos, on="bucket", how="left").fillna({"Reunião realizada": 0})

fig_evol = charts.serie_temporal(
    evol, x="bucket",
    series={"Criadas": BRAND_BLUE_600, "Perdidas": STATUS_CRITICAL, "Reunião realizada": STATUS_GOOD},
    area=True, altura=340,
)
st.plotly_chart(fig_evol, use_container_width=True, key=f"{PAGE}_evolucao")

st.divider()

# ---------------------------------------------------------------- Velocidade
st.subheader("Velocidade do funil")

v1, v2 = st.columns(2)

with v1:
    st.markdown("**Tempo parado em cada etapa**")
    st.caption(
        "Concluídas = já saíram da etapa (referência histórica). Paradas agora = "
        "ainda estão lá, contando até hoje -- é aqui que um gargalo atual aparece."
    )
    aging = query(
        "select deal_id, stage_name, stage_order, duration_hours, exited_at "
        "from v_deal_stage_aging where product_group = 'Máquina ISP'"
    )
    aging = aging[aging["deal_id"].isin(deals["deal_id"])]
    if aging.empty:
        st.caption("Sem dados de permanência no recorte atual.")
    else:
        # Misturar concluidas (exited_at preenchido) com paradas agora (exited_at
        # nulo, contando ate hoje) numa mediana so ESCONDE gargalo atual: mediana e
        # resistente a outlier por definicao, entao uma unica negociacao presa ha
        # semanas nao move a mediana nem um pouco -- e exatamente o caso que
        # importa mostrar. Por isso os dois grupos vem SEPARADOS, com o pior caso
        # (maximo) das paradas agora explicito, em vez de escondido atras da mediana.
        concluidas = aging[aging["exited_at"].notna()]
        paradas = aging[aging["exited_at"].isna()]

        # Trabalha em DIAS uteis daqui pra frente (nao horas) -- pedido do usuario,
        # pra nao misturar formato adaptativo "5d 6h" com um numero direto e facil
        # de comparar de etapa pra etapa. Converte assim que sai do agrupamento,
        # entao eixo, texto e hover ficam todos na mesma unidade sem conversao
        # espalhada pelo resto do codigo.
        ag_concluidas = (
            concluidas.groupby(["stage_name", "stage_order"])["duration_hours"]
            .agg(passagens="count", mediana="median").reset_index()
        )
        ag_concluidas["mediana_dias"] = ag_concluidas["mediana"] / 24
        ag_paradas = (
            paradas.groupby(["stage_name", "stage_order"])["duration_hours"]
            .agg(paradas="count", mediana="median", maximo="max").reset_index()
        )
        ag_paradas["mediana_dias"] = ag_paradas["mediana"] / 24
        ag_paradas["maximo_dias"] = ag_paradas["maximo"] / 24
        ordem_etapas = (
            aging[["stage_name", "stage_order"]].drop_duplicates()
            .sort_values("stage_order", ascending=False)["stage_name"].tolist()
        )

        fig = go.Figure()
        if not ag_concluidas.empty:
            base_c = ag_concluidas.set_index("stage_name").reindex(ordem_etapas).reset_index()
            fig.add_trace(go.Bar(
                name="Concluídas (mediana)", y=base_c["stage_name"], x=base_c["mediana_dias"],
                orientation="h", marker=dict(color=BRAND_BLUE_600),
                text=[format_days(h) for h in base_c["mediana"]],
                textposition="outside", cliponaxis=False,
                customdata=base_c[["passagens"]].values,
                hovertemplate="<b>%{y}</b><br>mediana (concluídas): %{x:.1f} dias<br>%{customdata[0]} passagens<extra></extra>",
            ))
        if not ag_paradas.empty:
            base_p = ag_paradas.set_index("stage_name").reindex(ordem_etapas).reset_index()
            fig.add_trace(go.Bar(
                name="Paradas agora (mediana)", y=base_p["stage_name"], x=base_p["mediana_dias"],
                orientation="h", marker=dict(color=STATUS_CRITICAL),
                text=[format_days(h) for h in base_p["mediana"]],
                textposition="outside", cliponaxis=False,
                customdata=base_p[["paradas", "maximo_dias"]].values,
                hovertemplate="<b>%{y}</b><br>mediana (paradas agora): %{x:.1f} dias<br>"
                "%{customdata[0]} paradas agora · pior caso: %{customdata[1]:.1f} dias<extra></extra>",
            ))
        fig.update_layout(barmode="group")
        fig.update_xaxes(showgrid=False, title_text="Dias úteis")
        base_layout(fig, height=340)
        fig.update_layout(margin=dict(l=10, r=10, t=20, b=10))
        st.plotly_chart(fig, use_container_width=True, key=f"{PAGE}_aging")

        # leitura automatica do pior caso -- a negociacao presa ha mais tempo AGORA,
        # em qualquer etapa, e o dado que a mediana sozinha nunca mostraria.
        if not ag_paradas.empty:
            pior = ag_paradas.loc[ag_paradas["maximo"].idxmax()]
            st.caption(
                f"⚠️ Pior caso agora: uma negociação parada há **{format_days(pior['maximo'])}** "
                f"em **{pior['stage_name']}**."
            )

with v2:
    st.markdown("**Tempo de movimentação entre etapas**")
    st.caption("Quanto tempo leva o salto de uma etapa para a seguinte.")
    trans = query(
        "select deal_id, de_etapa_nome, de_ordem, para_etapa_nome, transicao_horas "
        "from v_deal_stage_transitions where product_group = 'Máquina ISP'"
    )
    trans = trans[trans["deal_id"].isin(deals["deal_id"])]
    if trans.empty:
        st.caption("Sem movimentações registradas no recorte atual.")
    else:
        trans["movimento"] = trans["de_etapa_nome"] + " → " + trans["para_etapa_nome"]
        tr = (
            trans.groupby(["movimento", "de_ordem"])["transicao_horas"]
            .agg(transicoes="count", mediana="median").reset_index()
            .sort_values(["de_ordem", "movimento"], ascending=[False, True])
        )
        tr["mediana_dias"] = tr["mediana"] / 24  # ver comentario no grafico de aging acima
        fig = go.Figure(go.Bar(
            y=tr["movimento"], x=tr["mediana_dias"], orientation="h",
            marker=dict(color=CAT_ORANGE),
            text=[f"  {format_days(h)}" for h in tr["mediana"]],
            textposition="outside", cliponaxis=False,
            customdata=tr[["transicoes"]].values,
            hovertemplate="<b>%{y}</b><br>mediana: %{x:.1f} dias<br>%{customdata[0]} movimentações<extra></extra>",
        ))
        fig.update_xaxes(showgrid=False, showticklabels=False, range=[0, tr["mediana_dias"].max() * 1.4])
        base_layout(fig, height=330)
        fig.update_layout(showlegend=False, margin=dict(l=10, r=10, t=20, b=10))
        st.plotly_chart(fig, use_container_width=True, key=f"{PAGE}_transicoes")

st.divider()

# ---------------------------------------------------------------- Perdas
st.subheader("Anatomia das perdas")

p1, p2 = st.columns([2, 3])
perdas = deals[deals["deal_status"] == "lost"]
# base da rosca: sem o proprio filtro de motivo -- um grafico alimentado pelos
# dados que ele mesmo filtra fica com uma fatia so, e o Streamlit zera a selecao
# de um grafico cujos dados mudaram, desfazendo o drill sozinho.
perdas_base = deals_filtrado[deals_filtrado["deal_status"] == "lost"]

with p1:
    if perdas.empty:
        st.caption("Nenhuma negociação perdida no recorte.")
    else:
        agg = (
            perdas_base.groupby("motivo_perda")["deal_id"].nunique()
            .reset_index(name="negociacoes").sort_values("negociacoes", ascending=False)
        )
        ev = charts.selecionavel(
            charts.rosca(agg, dimensao="motivo_perda", valor="negociacoes",
                         centro=f"{format_int(len(perdas_base))}<br>perdidas", altura=340),
            key=f"{PAGE}_motivos",
        )
        interact.capture(ev, chart_key="motivos", dim="motivo_perda", page=PAGE)

with p2:
    if not perdas.empty:
        principal = perdas["motivo_perda"].value_counts()
        top_motivo, top_n = principal.index[0], int(principal.iloc[0])
        st.markdown("**Leitura**")
        st.markdown(
            f"- **{format_pct(100 * top_n / len(perdas))}** das perdas "
            f"({format_int(top_n)} de {format_int(len(perdas))}) são por **{top_motivo}**."
        )
        if "Sem Retorno" in str(top_motivo):
            st.markdown(
                "- Perda por *silêncio*, não por rejeição: o lead nunca chegou a dizer não. "
                "Isso aponta para **cadência e persistência de contato**, não para proposta ou preço — "
                "é o tipo de perda que responde a mais tentativas, não a mais desconto."
            )
        toques = perdas["atividades"].median()
        st.markdown(f"- Mediana de **{toques:.0f} atividade(s)** registrada(s) por negociação perdida.")
        sem_toque = int((perdas["atividades"] == 0).sum())
        if sem_toque:
            st.markdown(
                f"- ⚠️ **{format_int(sem_toque)}** perdas **sem nenhuma atividade registrada** — "
                "ou faltou trabalhar o lead, ou faltou registrar o trabalho no CRM."
            )

st.divider()

# ---------------------------------------------------------------- Detalhe
st.subheader("Negociações no recorte")

cols_detalhe = {
    "deal_name": "Negociação", "stage_name": "Etapa atual", "deal_status_label": "Status",
    "sdr_name": "SDR", "origem": "Origem", "cidade": "Cidade", "uf": "UF",
    "atividades": "Atividades", "dias_no_funil": "Dias no funil", "motivo_perda": "Motivo de perda",
}
tabela = deals[list(cols_detalhe)].copy()
tabela["dias_no_funil"] = tabela["dias_no_funil"].round(0)
tabela = tabela.rename(columns=cols_detalhe).sort_values("Dias no funil", ascending=False)

st.dataframe(tabela, use_container_width=True, hide_index=True, height=420)

st.download_button(
    "⬇️ Baixar recorte em CSV",
    tabela.to_csv(index=False).encode("utf-8-sig"),
    file_name="funil_maquina_isp.csv",
    mime="text/csv",
)
