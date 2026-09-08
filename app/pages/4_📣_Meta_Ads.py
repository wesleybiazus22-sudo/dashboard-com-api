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
    META_OBJECTIVE_LABELS,
    META_STATUS_COLORS,
    META_STATUS_LABELS,
    base_layout,
    format_int,
    format_money,
    format_pct,
    inject_brand,
    render_brand_header,
)

PAGE = "meta_ads"

st.set_page_config(page_title="Meta Ads", page_icon="📣", layout="wide")
inject_brand()

head_col, refresh_col = st.columns([4, 1.3])
with head_col:
    render_brand_header("📣 Meta Ads", "Performance de campanhas no Facebook/Instagram — investimento, alcance e conversão.")
with refresh_col:
    st.write("")
    st.write("")
    if st.button("🔄 Atualizar", use_container_width=True, help="Os dados ficam 5 min em cache"):
        st.cache_data.clear()
        st.rerun()

# ---------------------------------------------------------------- Carga
# Base granular: uma linha por (anuncio, dia) -- a MESMA granularidade em que o
# Meta devolve os insights. Toda agregacao da pagina (campanha, conjunto, anuncio,
# serie temporal) parte daqui, em vez de views pre-agregadas por nivel -- assim um
# unico carregamento sustenta os tres niveis de detalhe e os drills se propagam
# corretamente entre eles (clicar numa campanha tambem recorta seus conjuntos e
# anuncios, sem precisar recarregar nada).
granular_all = query(
    """
    select
        date,
        campaign_meta_id as campaign_id, campaign_name,
        adset_meta_id as adset_id, adset_name,
        ad_meta_id as ad_id, ad_name,
        spend as investimento, impressions as impressoes, clicks as cliques,
        leads_estimados, video_thruplay, video_view_50
    from v_meta_insights_enriched
    """
)
campanhas_all = query("select * from v_meta_campaign_performance")
adsets_dim = query("select meta_id as adset_id, status, effective_status from meta_adsets")
ads_dim = query("select meta_id as ad_id, status, effective_status, creative_thumbnail_url from meta_ads")

if granular_all.empty or campanhas_all.empty:
    st.info(
        "Nenhum dado do Meta Ads sincronizado ainda. Configure as credenciais "
        "(ver seção 12 do README) e rode `python -m ingestion.sync_all full`."
    )
    st.stop()

granular_all["date"] = pd.to_datetime(granular_all["date"])
campanhas_all["status_label"] = campanhas_all["status"].map(META_STATUS_LABELS).fillna(campanhas_all["status"])
campanhas_all["objetivo_label"] = campanhas_all["objective"].map(META_OBJECTIVE_LABELS).fillna(campanhas_all["objective"])

# ---------------------------------------------------------------- Filtros
with st.container(border=True):
    inicio, fim, _preset = filters.filtro_periodo(
        granular_all["date"].min(), granular_all["date"].max(), page=PAGE, label="Período",
        default="Desde agosto",
    )
    _gran_label, gran_regra = filters.filtro_granularidade(PAGE, default="Dia")

    f1, f2 = st.columns(2)
    with f1:
        sel_status = filters.multiselect_dim(campanhas_all, "status_label", "Status da campanha", page=PAGE)
    with f2:
        sel_objetivo = filters.multiselect_dim(campanhas_all, "objetivo_label", "Objetivo", page=PAGE)

granular_periodo = filters.recorta_periodo(granular_all, "date", inicio, fim)

campanhas_filtrado = filters.aplica_multiselects(
    campanhas_all, {"status_label": sel_status, "objetivo_label": sel_objetivo}
)
granular_filtrado = granular_periodo[granular_periodo["campaign_id"].isin(campanhas_filtrado["campaign_id"])]

interact.chips(PAGE)
drills = interact.active(PAGE)
diario = interact.apply(granular_filtrado, PAGE)

if diario.empty:
    st.warning("Nenhum dado no recorte atual. Ajuste o período ou os filtros.")
    st.stop()

campanhas_no_recorte = campanhas_filtrado[campanhas_filtrado["campaign_id"].isin(diario["campaign_id"])]

st.caption(
    f"**{format_int(campanhas_no_recorte['campaign_id'].nunique())}** campanha(s), "
    f"**{format_int(diario['adset_id'].nunique())}** conjunto(s), "
    f"**{format_int(diario['ad_id'].nunique())}** anúncio(s) no recorte atual."
)

# ---------------------------------------------------------------- KPIs
investimento = diario["investimento"].sum()
impressoes = int(diario["impressoes"].sum())
cliques = int(diario["cliques"].sum())
leads = diario["leads_estimados"].sum()
ctr = 100 * cliques / impressoes if impressoes else None
cpc = investimento / cliques if cliques else None
custo_por_lead = investimento / leads if leads else None

# Leads REAIS confirmados no CRM, cruzados por campanha via UTM -- diferente do
# "leads estimados" acima (que vem do array de acoes do Meta, autodeclarado pela
# plataforma). Aqui contamos negociacoes de verdade que caem no CRM com o campo
# personalizado `utm_medium` preenchido, que guarda o NOME da campanha do Meta
# (confirmado contra a base real -- bate exatamente com `meta_campaigns.name`,
# apesar do nome do campo sugerir "medio" -- e como a tag de rastreio foi
# configurada no lado do RD/Meta, fora do escopo deste repositorio).
#
# LIMITACAO IMPORTANTE: essa captura de UTM no card da negociacao e recente --
# hoje so uma fracao pequena das negociacoes carrega esse dado (a maioria das
# negociacoes antigas nao tem). Por isso "custo real por lead" aqui tende a vir
# MAIOR do que o real de verdade (o investimento total esta sendo dividido por
# uma contagem de leads que ainda esta incompleta) -- e uma metrica que fica mais
# precisa com o tempo, a medida que mais negociacoes acumularem esse rastreio,
# nao um numero definitivo hoje.
crm_leads_utm = query(
    """
    select raw->'custom_fields'->>'utm_medium' as campaign_name, deal_created_at::date as date
    from crm_deals
    where raw->'custom_fields'->>'utm_medium' is not null
    """
)
if not crm_leads_utm.empty:
    crm_leads_utm = filters.recorta_periodo(crm_leads_utm, "date", inicio, fim)
    crm_leads_utm = crm_leads_utm[crm_leads_utm["campaign_name"].isin(campanhas_no_recorte["campaign_name"])]
leads_reais_crm = len(crm_leads_utm) if not crm_leads_utm.empty else 0
custo_real_por_lead = investimento / leads_reais_crm if leads_reais_crm else None

k1, k2, k3 = st.columns(3)
k4, k5, k6 = st.columns(3)
k7, k8 = st.columns(2)
k1.metric("Investido", format_money(investimento))
k2.metric("Impressões", format_int(impressoes))
k3.metric("Cliques", format_int(cliques))
k4.metric("CTR", format_pct(ctr, 2))
k5.metric("CPC médio", format_money(cpc))
k6.metric(
    "Leads estimados", format_int(leads),
    format_money(custo_por_lead) + "/lead" if custo_por_lead else None,
    help="Extraído do array de ações do Meta -- soma qualquer action_type que contenha "
    "'lead' (não há um tipo único e universal para isso na API).",
)
k7.metric(
    "Leads reais (CRM)", format_int(leads_reais_crm),
    help="Negociações de verdade no CRM, cruzadas com a campanha do Meta pela UTM "
    "gravada no card. Amostra ainda pequena -- esse rastreio começou a ser capturado "
    "recentemente, a maioria das negociações antigas não tem essa informação. Cresce "
    "em precisão com o tempo.",
)
k8.metric(
    "Custo real por lead (CRM)", format_money(custo_real_por_lead) if custo_real_por_lead else "—",
    help="Investimento total do recorte ÷ leads reais confirmados no CRM (não os "
    "estimados pelo Meta). Como nem toda negociação real ainda carrega UTM, esse "
    "número tende a estar SUPERESTIMADO hoje (menos leads contados do que os que "
    "realmente existem) -- fica mais confiável conforme mais negociações acumularem "
    "o rastreio.",
)

st.divider()

# ---------------------------------------------------------------- Evolucao temporal
st.subheader("Investimento no tempo")

periodo_pandas = {"D": "D", "W-MON": "W-MON", "MS": "M"}[gran_regra]
serie = diario.copy()
serie["bucket"] = serie["date"].dt.to_period(periodo_pandas).dt.start_time
evol = serie.groupby("bucket").agg(
    investimento=("investimento", "sum"), cliques=("cliques", "sum"), leads=("leads_estimados", "sum")
).reset_index()

fig_evol = go.Figure()
fig_evol.add_trace(go.Scatter(
    x=evol["bucket"], y=evol["investimento"], name="Investimento (R$)", mode="lines+markers",
    line=dict(color=BRAND_BLUE_600, width=2.5, shape="spline", smoothing=0.5),
    marker=dict(size=6), fill="tozeroy", fillcolor="rgba(0,87,234,0.08)",
    hovertemplate="<b>%{x|%d/%m/%Y}</b><br>R$ %{y:.2f}<extra></extra>",
))
fig_evol.add_trace(go.Scatter(
    x=evol["bucket"], y=evol["cliques"], name="Cliques", mode="lines+markers", yaxis="y2",
    line=dict(color=CAT_ORANGE, width=2, dash="dot"), marker=dict(size=6),
    hovertemplate="<b>%{x|%d/%m/%Y}</b><br>%{y} cliques<extra></extra>",
))
fig_evol.update_layout(
    yaxis=dict(title="Investimento (R$)", rangemode="tozero"),
    yaxis2=dict(title="Cliques", overlaying="y", side="right", rangemode="tozero", showgrid=False),
)
fig_evol.update_xaxes(showgrid=False)
base_layout(fig_evol, height=360)
st.plotly_chart(fig_evol, use_container_width=True, key=f"{PAGE}_evolucao")

st.divider()

# ---------------------------------------------------------------- Performance por campanha
st.subheader("Performance por campanha")
st.caption("Clique em uma barra para recortar a página inteira por aquela campanha (inclusive os conjuntos e anúncios abaixo).")

# base SEM o proprio drill de campanha -- senao clicar numa campanha reduziria o
# grafico a uma barra so, e o Streamlit descartaria a selecao (ver app/interact.py).
base_campanha = interact.apply(granular_filtrado, PAGE, ignorar=("campaign_name",))
agg_campanha = (
    base_campanha.groupby("campaign_id")
    .agg(investimento=("investimento", "sum"), cliques=("cliques", "sum"),
         impressoes=("impressoes", "sum"), leads=("leads_estimados", "sum"))
    .reset_index()
    .merge(campanhas_all[["campaign_id", "campaign_name", "status_label"]], on="campaign_id", how="left")
    .sort_values("investimento", ascending=False)
)
agg_campanha["campaign_name"] = agg_campanha["campaign_name"].fillna("(sem nome)")
agg_campanha["custo_por_lead"] = (agg_campanha["investimento"] / agg_campanha["leads"]).where(agg_campanha["leads"] > 0)

c1, c2 = st.columns([3, 2])

with c1:
    ev = charts.selecionavel(
        charts.barra_dimensao(
            agg_campanha, dimensao="campaign_name", valor="investimento",
            cor_padrao=BRAND_BLUE_600, altura=420, maximo_itens=15,
        ),
        key=f"{PAGE}_campanhas",
    )
    interact.capture(ev, chart_key="campanhas", dim="campaign_name", page=PAGE)

with c2:
    st.markdown("**Composição por status**")
    agg_status = (
        campanhas_no_recorte.groupby("status_label")["campaign_id"].nunique()
        .reset_index(name="campanhas")
    )
    fig_status = charts.rosca(
        agg_status, dimensao="status_label", valor="campanhas",
        cores={META_STATUS_LABELS.get(k, k): v for k, v in META_STATUS_COLORS.items()},
        centro=f"{format_int(len(campanhas_no_recorte))}<br>campanhas", altura=300,
    )
    st.plotly_chart(fig_status, use_container_width=True, key=f"{PAGE}_status")

with st.expander(f"Ver leads por campanha ({format_int(len(agg_campanha))} campanhas)"):
    cols_campanha = {
        "campaign_name": "Campanha", "status_label": "Status", "investimento": "Investido",
        "impressoes": "Impressões", "cliques": "Cliques", "leads": "Leads estimados",
        "custo_por_lead": "Custo/lead",
    }
    tabela_campanha = (
        agg_campanha[list(cols_campanha)].rename(columns=cols_campanha).sort_values("Leads estimados", ascending=False)
    )
    tabela_campanha["Investido"] = tabela_campanha["Investido"].apply(format_money)
    tabela_campanha["Custo/lead"] = tabela_campanha["Custo/lead"].apply(format_money)
    st.dataframe(tabela_campanha, use_container_width=True, hide_index=True, height=300)

st.divider()

# ---------------------------------------------------------------- Performance por conjunto
st.subheader("Performance por conjunto de anúncios")
st.caption("Segmentação/público dentro de cada campanha. Clique numa barra para recortar também os anúncios abaixo.")

base_adset = interact.apply(granular_filtrado, PAGE, ignorar=("adset_name",))
agg_adset = (
    base_adset.groupby("adset_id")
    .agg(
        adset_name=("adset_name", "first"), campaign_name=("campaign_name", "first"),
        investimento=("investimento", "sum"), impressoes=("impressoes", "sum"),
        cliques=("cliques", "sum"), leads=("leads_estimados", "sum"),
    )
    .reset_index()
    .merge(adsets_dim[["adset_id", "status"]], on="adset_id", how="left")
    .sort_values("investimento", ascending=False)
)
agg_adset["adset_name"] = agg_adset["adset_name"].fillna("(sem nome)")
agg_adset["status_label"] = agg_adset["status"].map(META_STATUS_LABELS).fillna(agg_adset["status"])
agg_adset["ctr_pct"] = (100 * agg_adset["cliques"] / agg_adset["impressoes"]).round(2)
agg_adset["cpc"] = (agg_adset["investimento"] / agg_adset["cliques"]).round(2)
agg_adset["custo_por_lead"] = (agg_adset["investimento"] / agg_adset["leads"]).where(agg_adset["leads"] > 0)

if agg_adset.empty:
    st.caption("Nenhum conjunto de anúncios no recorte atual.")
else:
    ev = charts.selecionavel(
        charts.barra_dimensao(
            agg_adset, dimensao="adset_name", valor="investimento",
            cor_padrao=CAT_ORANGE, altura=max(280, 26 * min(len(agg_adset), 15)), maximo_itens=15,
        ),
        key=f"{PAGE}_adsets",
    )
    interact.capture(ev, chart_key="adsets", dim="adset_name", page=PAGE)

    with st.expander(f"Ver tabela completa ({format_int(len(agg_adset))} conjuntos)"):
        cols_adset = {
            "adset_name": "Conjunto", "campaign_name": "Campanha", "status_label": "Status",
            "investimento": "Investido", "impressoes": "Impressões", "cliques": "Cliques",
            "ctr_pct": "CTR %", "cpc": "CPC", "leads": "Leads estimados", "custo_por_lead": "Custo/lead",
        }
        tabela_adset = agg_adset[list(cols_adset)].rename(columns=cols_adset)
        tabela_adset["Investido"] = tabela_adset["Investido"].apply(format_money)
        tabela_adset["CPC"] = tabela_adset["CPC"].apply(format_money)
        tabela_adset["Custo/lead"] = tabela_adset["Custo/lead"].apply(format_money)
        st.dataframe(tabela_adset, use_container_width=True, hide_index=True, height=360)

st.divider()

# ---------------------------------------------------------------- Performance por anuncio (+ criativo)
st.subheader("Anúncios e criativos")
st.caption(
    "Nível mais granular: cada anúncio individual, com a miniatura do criativo usado. "
    "Respeita os recortes de campanha/conjunto acima."
)

agg_ad = (
    diario.groupby("ad_id")
    .agg(
        ad_name=("ad_name", "first"), campaign_name=("campaign_name", "first"),
        adset_name=("adset_name", "first"), investimento=("investimento", "sum"),
        impressoes=("impressoes", "sum"), cliques=("cliques", "sum"), leads=("leads_estimados", "sum"),
        # min_count=1: se TODAS as linhas do anuncio forem NULL (nao e video), o
        # pandas por padrao somaria pra 0 -- min_count=1 mantem NaN nesse caso, pra
        # nao confundir "nao e video" com "e video, mas ninguem assistiu nada".
        thruplay=("video_thruplay", lambda s: s.sum(min_count=1)),
        view_50=("video_view_50", lambda s: s.sum(min_count=1)),
    )
    .reset_index()
    .merge(ads_dim[["ad_id", "status", "creative_thumbnail_url"]], on="ad_id", how="left")
)
agg_ad["ad_name"] = agg_ad["ad_name"].fillna("(sem nome)")
agg_ad["status_label"] = agg_ad["status"].map(META_STATUS_LABELS).fillna(agg_ad["status"])
agg_ad["ctr_pct"] = (100 * agg_ad["cliques"] / agg_ad["impressoes"]).round(2)
agg_ad["cpc"] = (agg_ad["investimento"] / agg_ad["cliques"]).round(2)
# custo_por_lead só faz sentido pra quem de fato gerou lead -- com leads=0 a divisao
# daria inf, que polui ordenacao/exibicao sem informar nada (custo por lead
# "infinito" nao e um numero, e "nao aconteceu ainda").
agg_ad["custo_por_lead"] = (agg_ad["investimento"] / agg_ad["leads"]).where(agg_ad["leads"] > 0)

if agg_ad.empty:
    st.caption("Nenhum anúncio no recorte atual.")
else:
    tem_leads = agg_ad["leads"].sum() > 0
    tem_video = agg_ad["thruplay"].notna().any()
    # Leads primeiro e como padrao quando existe -- e a metrica de negocio real;
    # investimento/cliques/video sao pistas de qualidade de trafego, nao o resultado.
    opcoes_ordem = {}
    if tem_leads:
        opcoes_ordem["Leads"] = "leads"
    opcoes_ordem["Investimento"] = "investimento"
    opcoes_ordem["Cliques"] = "cliques"
    if tem_video:
        opcoes_ordem["ThruPlay"] = "thruplay"
        opcoes_ordem["Assistiu 50%"] = "view_50"
    ordem_label = st.radio("Ordenar por", list(opcoes_ordem), horizontal=True, key=f"{PAGE}_ordem_ad")
    agg_ad_ordenado = agg_ad.sort_values(opcoes_ordem[ordem_label], ascending=False, na_position="last")

    st.markdown(f"**Top anúncios por {ordem_label.lower()}** (de {format_int(len(agg_ad))} no recorte)")

    if tem_leads:
        melhor = agg_ad.loc[agg_ad["custo_por_lead"].idxmin()]
        # Escapa o "$" de format_money nesta mensagem: com DOIS valores em R$ na
        # mesma chamada de markdown, o Streamlit interpreta o par de "$" como
        # abertura/fechamento de formula LaTeX (nao e bug de digitacao -- e como o
        # st.success/markdown trata "$...$"). Com um so valor por chamada isso nao
        # acontece (nao ha par pra fechar), por isso o resto da pagina nao precisa
        # dessa escapada.
        custo_fmt = format_money(melhor["custo_por_lead"]).replace("$", r"\$")
        investimento_fmt = format_money(melhor["investimento"]).replace("$", r"\$")
        st.success(
            f"🎯 Melhor custo por lead no recorte: **{melhor['ad_name']}** — "
            f"{format_int(int(melhor['leads']))} lead(s) a {custo_fmt} cada "
            f"(investiu {investimento_fmt} em {format_int(int(melhor['cliques']))} cliques)."
        )

    top_ads = list(agg_ad_ordenado.head(8).iterrows())
    for inicio_linha in range(0, len(top_ads), 4):
        colunas_galeria = st.columns(4)
        for col, (_, row) in zip(colunas_galeria, top_ads[inicio_linha:inicio_linha + 4]):
            with col, st.container(border=True):
                if pd.notna(row["creative_thumbnail_url"]) and row["creative_thumbnail_url"]:
                    try:
                        st.image(row["creative_thumbnail_url"], width=120)
                    except Exception:
                        st.caption("🖼️ imagem indisponível")
                else:
                    st.caption("🖼️ sem criativo registrado")

                nome = row["ad_name"] if len(row["ad_name"]) <= 40 else row["ad_name"][:37] + "..."
                st.markdown(f"**{nome}**")
                st.caption(f"{row['campaign_name'] or ''}")

                if row["leads"] > 0:
                    st.markdown(f"🎯 **{format_int(int(row['leads']))} lead(s)** · {format_money(row['custo_por_lead'])}/lead")
                elif tem_leads:
                    # so avisa "0 leads" quando ALGUM anuncio do recorte gerou lead --
                    # senao toda campanha de topo de funil ficaria com essa linha
                    # cinza em todo card, o que e ruido (nenhum devia gerar lead mesmo).
                    st.caption("🎯 0 leads no recorte")

                st.markdown(
                    f"💰 {format_money(row['investimento'])}  \n"
                    f"👆 {format_int(int(row['cliques']))} cliques · CTR {format_pct(row['ctr_pct'], 2)}"
                )
                if pd.notna(row["thruplay"]) or pd.notna(row["view_50"]):
                    st.markdown(
                        f"▶️ ThruPlay: {format_int(row['thruplay']) if pd.notna(row['thruplay']) else '—'}  \n"
                        f"👀 Assistiu 50%: {format_int(row['view_50']) if pd.notna(row['view_50']) else '—'}"
                    )

    with st.expander(f"Ver tabela completa ({format_int(len(agg_ad))} anúncios)"):
        cols_ad = {
            "ad_name": "Anúncio", "campaign_name": "Campanha", "adset_name": "Conjunto",
            "status_label": "Status", "investimento": "Investido", "impressoes": "Impressões",
            "cliques": "Cliques", "ctr_pct": "CTR %", "cpc": "CPC", "leads": "Leads estimados",
            "custo_por_lead": "Custo/lead", "thruplay": "ThruPlay", "view_50": "Assistiu 50%",
        }
        tabela_ad = agg_ad_ordenado[list(cols_ad)].rename(columns=cols_ad)
        tabela_ad["Investido"] = tabela_ad["Investido"].apply(format_money)
        tabela_ad["CPC"] = tabela_ad["CPC"].apply(format_money)
        tabela_ad["Custo/lead"] = tabela_ad["Custo/lead"].apply(format_money)
        st.dataframe(tabela_ad, use_container_width=True, hide_index=True, height=420)

        st.download_button(
            "⬇️ Baixar anúncios em CSV",
            agg_ad_ordenado.rename(columns=cols_ad).to_csv(index=False).encode("utf-8-sig"),
            file_name="meta_ads_anuncios.csv",
            mime="text/csv",
        )

st.divider()
st.caption(
    "ℹ️ O cruzamento de custo por LEAD real (ver \"Custo real por lead (CRM)\" acima) já "
    "usa a UTM gravada no card da negociação -- mas ainda cobre uma fração pequena das "
    "negociações (a captura começou recentemente). Cruzamento mais fundo no funil (custo "
    "por reunião realizada, por venda fechada) ainda não existe -- só a etapa de lead."
)
