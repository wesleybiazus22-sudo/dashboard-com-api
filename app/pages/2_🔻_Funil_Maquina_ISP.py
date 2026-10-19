import sys
from datetime import date
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

head_col, refresh_col = st.columns([4, 1.3])
with head_col:
    render_brand_header("Funil Máquina ISP", "Da prospecção ao Freemium — volume, conversão, velocidade e geografia.")
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

for _col in ("deal_created_at", "closed_at", "sdr_ganhou_at", "closer_ganhou_at"):
    deals_all[_col] = pd.to_datetime(deals_all[_col], utc=True, errors="coerce")
deals_all["deal_status_label"] = deals_all["deal_status"].map(DEAL_STATUS_LABELS).fillna(deals_all["deal_status"])

# Relogio do "tempo no funil" so comeca quando a negociacao entra em Em Prospeccao
# (definicao do dono do produto) -- Primeira Conexao e so o contato do Melhor Venda,
# ainda nao e prospeccao de verdade. Sem data registrada de entrada (negociacao que
# nunca chegou la, ou lacuna de sincronizacao antiga: o historico so comeca numa
# etapa posterior), o relogio fica vazio em vez de usar a data de criacao, que
# reintroduziria o tempo de Primeira Conexao que a definicao quer excluir.
_em_prospeccao = query(
    "select sh.deal_rd_id as deal_id, min(sh.entered_at) as em_prospeccao_at "
    "from crm_deal_stage_history sh "
    "join crm_stages s on s.rd_id = sh.stage_rd_id "
    "join crm_pipelines p on p.rd_id = sh.pipeline_rd_id "
    "where p.product_group = 'Máquina ISP' and s.name = 'Em Prospecção' "
    "group by sh.deal_rd_id"
)
deals_all = deals_all.merge(_em_prospeccao, on="deal_id", how="left")
deals_all["em_prospeccao_at"] = pd.to_datetime(deals_all["em_prospeccao_at"], utc=True, errors="coerce")
_fim_relogio = deals_all["closed_at"].fillna(pd.Timestamp.now(tz="UTC"))
deals_all["dias_desde_prospeccao"] = (_fim_relogio - deals_all["em_prospeccao_at"]).dt.total_seconds() / 86400
deals_all.loc[deals_all["dias_desde_prospeccao"] < 0, "dias_desde_prospeccao"] = float("nan")

alcance_all = query("select * from v_maquina_isp_stage_reach")

# ---------------------------------------------------------------- Filtros
with st.container(border=True):
    fotografia_atual = st.checkbox(
        "📸 Negociações em andamento agora (ignora a data de criação)",
        key=f"fotografia::{PAGE}",
        help=(
            "Mostra só quem está EM ANDAMENTO hoje -- não o histórico de já ganhas/perdidas --, "
            "ignorando a data em que cada uma entrou. Use isso pra apresentar o funil ativo pra "
            "diretoria: sem isso, uma negociação antiga que ainda está ativa hoje pode sumir do "
            "recorte só porque ela entrou fora do período escolhido, e negociações já concluídas "
            "(ganhas ou perdidas) inflam os números sem representar o pipeline de verdade."
        ),
    )

    c_data, c_gran = st.columns([3, 1])
    inicio = fim = None
    with c_data:
        if fotografia_atual:
            st.markdown("**Período de criação da negociação**")
            st.caption(
                "🔒 Desativado em modo Em andamento agora -- mostra toda negociação com "
                "status 'Em andamento', não importa quando foi criada (já ganhas/perdidas ficam de fora)."
            )
            deals_periodo = deals_all[deals_all["deal_status"] == "ongoing"]
        else:
            inicio, fim, _preset = filters.filtro_periodo(
                deals_all["deal_created_at"].min(),
                deals_all["deal_created_at"].max(),
                page=PAGE,
                label="Período de criação da negociação",
            )
            deals_periodo = filters.recorta_periodo(deals_all, "deal_created_at", inicio, fim)
    with c_gran:
        _gran_label, gran_regra = filters.filtro_granularidade(PAGE)

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

selecoes_dim = {
    "origem": sel_origem,
    "regiao": sel_regiao,
    "uf": sel_uf,
    "sdr_name": sel_sdr,
    "deal_status_label": sel_status,
}
deals_filtrado = filters.aplica_multiselects(deals_periodo, selecoes_dim)

# drill (clique nos graficos) por cima dos filtros explicitos
interact.chips(PAGE)
drills = interact.active(PAGE)
deals = interact.apply(deals_filtrado, PAGE)

# Mesmos filtros de dimensao e drills, mas SEM o recorte por data de criacao --
# base das metricas contadas pela data em que o EVENTO aconteceu (Reunião
# realizada), nao pela data em que a negociacao entrou: um lead criado em julho
# que so teve reuniao em setembro tem que contar em setembro, e o recorte por
# criacao o deixaria de fora de qualquer periodo que nao inclua julho.
eventos_base = interact.apply(filters.aplica_multiselects(deals_all, selecoes_dim), PAGE)
if fim is not None:
    # Os presets cortam `fim` no ultimo dia com negociacao CRIADA -- pra eventos, um
    # periodo que vai ate o fim da base vai ate hoje (uma reuniao de hoje nao pode
    # sumir so porque nenhuma negociacao nova foi criada hoje).
    _ultima_criacao = deals_all["deal_created_at"].max().date()
    fim_eventos = date.today() if fim >= _ultima_criacao else fim
    reunioes_periodo = filters.recorta_periodo(
        eventos_base[eventos_base["sdr_ganhou"]], "sdr_ganhou_at", inicio, fim_eventos
    )
else:
    fim_eventos = None
    reunioes_periodo = deals[deals["sdr_ganhou"]]

if deals.empty:
    st.warning("Nenhuma negociação com essa combinação de filtros. Remova algum filtro acima.")
    st.stop()

st.caption(
    f"**{format_int(len(deals))}** negociações no recorte atual "
    f"(de {format_int(len(deals_all))} no escopo total do produto)."
)

# ---------------------------------------------------------------- KPIs
total = len(deals)
reunioes = int(reunioes_periodo["deal_id"].nunique())
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
medidas_relogio = deals["dias_desde_prospeccao"].dropna()
mediana_dias = medidas_relogio.median() if not medidas_relogio.empty else None

# Toda porcentagem diz sobre QUAL base foi calculada -- "do total"/"de conversão"
# sozinhos nao deixavam claro se a base era o recorte, o funil ou outra coisa.
base_recorte = "em andamento" if fotografia_atual else "do recorte"

st.subheader("Visão geral (em andamento agora)" if fotografia_atual else "Visão geral do período")
k1, k2, k3 = st.columns(3)
k4, k5, k6 = st.columns(3)
k1.metric(
    "Negociações", format_int(total),
    help="Negociações em andamento hoje, incluídas nos filtros atuais (já ganhas/perdidas ficam de fora)." if fotografia_atual
    else "Negociações criadas no período e incluídas nos filtros atuais.",
)
k2.metric(
    "Em andamento", format_int(andamento),
    f"{format_pct(100 * andamento / total)} das {format_int(total)} {base_recorte}", delta_color="off",
)
if fotografia_atual:
    k3.metric(
        "Reunião realizada", format_int(reunioes),
        f"{format_pct(100 * reunioes / total)} das {format_int(total)} em andamento", delta_color="off",
        help="Das negociações em andamento agora, quantas já passaram por Reunião Realizada (ou além).",
    )
else:
    k3.metric(
        "Reunião realizada", format_int(reunioes),
        f"equivale a {format_pct(100 * reunioes / total)} das {format_int(total)} criadas no período", delta_color="off",
        help=(
            "Contada pela DATA EM QUE A NEGOCIAÇÃO CHEGOU em Reunião Realizada, não pela data de criação: "
            "uma negociação criada em julho que teve a reunião em setembro conta em setembro. "
            "Por isso pode ser diferente da barra de Reunião Realizada no funil abaixo, que mostra só as "
            "negociações CRIADAS no período que chegaram até lá. A porcentagem compara reuniões do "
            "período com negociações criadas no mesmo período -- é uma razão de volume, não a taxa de "
            "conversão de uma mesma turma de leads."
        ),
    )
k4.metric(
    "Freemium (ativos agora)", format_int(freemium_ativos),
    f"{format_int(closer_ganhos)} chegaram lá ao todo", delta_color="off",
    help=(
        "Negociações que estão NA ETAPA Freemium neste exato momento. "
        f"No total, {format_int(closer_ganhos)} já chegaram a Freemium alguma vez -- "
        "esse número maior inclui as que saíram depois (ex: para 'Desistiu') e "
        "continua contando como fechamento de verdade do closer, mesmo que a "
        "negociação não esteja mais ativa lá hoje."
    ),
)
k5.metric(
    "Perdidas", format_int(perdidas),
    f"{format_pct(100 * perdidas / total)} das {format_int(total)} {base_recorte}", delta_color="off",
)
k6.metric(
    "Tempo mediano no funil",
    format_duration(mediana_dias * 24) if mediana_dias is not None else "—",
    f"medido em {format_int(len(medidas_relogio))} das {format_int(total)}", delta_color="off",
    help=(
        "Começa a contar quando a negociação ENTRA em Em Prospecção (Primeira Conexão não conta -- "
        "é só o contato inicial do Melhor Venda) e vai até a negociação ser ganha/perdida, ou até hoje "
        "se ainda está aberta. Só entram na conta as negociações com data de entrada em Em Prospecção "
        "registrada: as que ainda estão em Primeira Conexão não começaram a contar, e algumas antigas "
        "não têm essa data no histórico (lacuna de sincronização) -- nenhuma delas é estimada."
    ),
)

st.divider()

# ---------------------------------------------------------------- Funil
st.subheader("Funil de conversão")

alcance = alcance_all[alcance_all["deal_id"].isin(deals["deal_id"])]
if alcance.empty:
    st.info("Sem histórico de etapas para as negociações filtradas.")
else:
    funil_df = (
        alcance.groupby(["passo", "etapa"])["deal_id"].nunique()
        .reset_index(name="negociacoes").sort_values("passo").reset_index(drop=True)
    )
    # Preserva inclusive etapas sem alcance no recorte atual.
    etapas = alcance_all[["passo", "etapa"]].drop_duplicates().sort_values("passo")
    funil_df = etapas.merge(funil_df, on=["passo", "etapa"], how="left")
    funil_df["negociacoes"] = funil_df["negociacoes"].fillna(0).astype(int)
    funil_df = funil_df.reset_index(drop=True)
    topo = funil_df["negociacoes"].max()
    funil_df["anterior"] = funil_df["negociacoes"].shift(1)
    funil_df["retencao_topo_pct"] = (100 * funil_df["negociacoes"] / topo).round(1)
    funil_df["perdidas_no_passo"] = (
        funil_df["anterior"].fillna(funil_df["negociacoes"]) - funil_df["negociacoes"]
    ).astype(int)
    funil_df["queda_pp_topo"] = funil_df["retencao_topo_pct"].shift(1) - funil_df["retencao_topo_pct"]

    sem_etapa = len(deals) - alcance["deal_id"].nunique()
    st.caption(
        f"Cada barra é quantas negociações **alcançaram** aquela etapa (por histórico ou por estarem nela/adiante) — "
        f"não quantas estão paradas nela hoje. **Toda porcentagem é sobre o topo**: as "
        f"**{format_int(topo)}** negociações que entraram no funil. Ex: 30% em Reunião Agendada = de cada 100 "
        f"que entraram, 30 chegaram lá."
        + (
            f" {format_int(sem_etapa)} negociações do recorte não aparecem aqui por não terem nenhuma etapa do "
            f"funil registrada (ex: criadas direto em Encerrado/Standby)."
            if sem_etapa > 0 else ""
        )
    )

    fc1, fc2 = st.columns([3, 2])
    with fc1:
        # NAO clicavel de proposito: o funil e o grafico que mais precisa REAGIR aos
        # drills (ver o funil so de Melhor Venda, so do Sul, etc). Um grafico clicavel
        # precisa manter a spec estavel entre reruns pro Streamlit nao descartar a
        # selecao -- as duas coisas sao incompativeis, e reagir vale mais aqui.
        st.plotly_chart(charts.funil(funil_df), use_container_width=True, key=f"{PAGE}_funil", config={"displayModeBar": False})
    with fc2:
        st.markdown("**Ainda não avançaram**")
        st.caption(
            "Quantas ficaram em cada passagem, em número e em % do topo. Inclui negociações em andamento "
            "e perdidas; não equivale a perdas no CRM."
        )
        fig_queda = charts.queda_entre_etapas(funil_df)
        if fig_queda is not None:
            st.plotly_chart(fig_queda, use_container_width=True, key=f"{PAGE}_queda", config={"displayModeBar": False})
        else:
            st.caption("Nenhuma queda entre etapas no recorte atual.")

    # leitura automatica do maior gargalo -- medido em pontos percentuais DO TOPO (onde
    # o funil perde mais gente em relacao a quem entrou), mesma base de todo o resto
    # do funil, em vez de conversao etapa-a-etapa
    gargalo = funil_df[funil_df["queda_pp_topo"] > 0].nlargest(1, "queda_pp_topo")
    if not gargalo.empty:
        g = gargalo.iloc[0]
        anterior = funil_df.iloc[int(g.name) - 1]
        st.info(
            f"🔎 **Maior gargalo:** entre **{anterior['etapa']}** e **{g['etapa']}** o funil cai de "
            f"**{format_pct(anterior['retencao_topo_pct'])}** para **{format_pct(g['retencao_topo_pct'])}** do topo — "
            f"{format_int(g['perdidas_no_passo'])} negociações ({format_pct(g['queda_pp_topo'])} de quem entrou no funil) "
            f"ainda não passaram dessa etapa. Compare volume, tempo de maturação e motivos de perda antes de priorizar uma ação."
        )

st.divider()

# ---------------------------------------------------------------- Snapshot atual
st.subheader("Fotografia atual do funil")
# De proposito parte de `deals_all`, NAO de `deals`: a fotografia e o retrato da
# pipeline inteira AGORA (o mesmo quadro que se ve abrindo o CRM), entao nao pode
# encolher com periodo de criacao, filtros nem cliques nos graficos -- antes ela
# saia do recorte filtrado e incluia perdidas (centenas em Encerrado/Standby), e
# por isso nunca batia com o CRM.
pipeline_agora = deals_all[deals_all["deal_status"] == "ongoing"].copy()
pausadas = int((deals_all["deal_status"] == "paused").sum())
st.caption(
    f"Onde estão paradas, **neste instante**, as **{format_int(len(pipeline_agora))}** negociações com status "
    f"**Em andamento** da pipeline inteira do Máquina ISP — o mesmo retrato do quadro do CRM. **Não muda com o "
    f"período, os filtros nem os cliques** acima. A % é a fatia do total em andamento em cada etapa."
    + (f" {format_int(pausadas)} negociações pausadas ficam de fora." if pausadas else "")
)
# Mesma ordenacao global usada em v_maquina_isp_stage_reach (etapas do pipeline
# Closer vem sempre depois das da Qualificacao, com o mesmo "+100") -- mas aqui
# SEM excluir as etapas terminais: uma negociacao em andamento parada em No-show
# ou Encerrado/Standby continua aparecendo nessa coluna do CRM, e tem que aparecer
# aqui tambem.
pipeline_agora["passo"] = pipeline_agora["stage_order"] + pipeline_agora["pipeline_name"].map(
    lambda p: 100 if p == "[Máquina ISP] Closer" else 0
)
snapshot_df = (
    pipeline_agora.groupby(["passo", "stage_name"])["deal_id"].nunique()
    .reset_index(name="negociacoes")
    .rename(columns={"stage_name": "etapa"})
)
st.plotly_chart(
    charts.snapshot_estagios(snapshot_df), use_container_width=True,
    key=f"{PAGE}_snapshot", config={"displayModeBar": False},
)

st.divider()

# ---------------------------------------------------------------- Evolucao mensal
st.subheader("Evolução mensal do funil")
st.caption(
    "Volume de negociações criadas por mês (linha cheia, eixo esquerdo) contra 3 taxas "
    "calculadas sobre o volume do próprio mês (linhas pontilhadas, eixo direito): quanto "
    "chegou em Reunião Realizada, quanto chegou em Freemium e quanto foi perdido. Taxa em "
    "vez de volume bruto isola mudança de EFICIÊNCIA (melhor/pior conversão) de mudança de "
    "VOLUME de entrada -- as duas coisas juntas costumam se confundir num gráfico só de contagem."
)
mensal = deals.copy()
mensal["mes"] = mensal["deal_created_at"].dt.tz_convert(None).dt.to_period("M").dt.start_time
evolucao_mensal = mensal.groupby("mes").agg(
    criadas=("deal_id", "nunique"),
    sdr_ok=("sdr_ganhou", "sum"),
    closer_ok=("closer_ganhou", "sum"),
    perdidas=("deal_status", lambda s: int((s == "lost").sum())),
).reset_index()
evolucao_mensal["pct_sdr"] = (100 * evolucao_mensal["sdr_ok"] / evolucao_mensal["criadas"]).round(1)
evolucao_mensal["pct_closer"] = (100 * evolucao_mensal["closer_ok"] / evolucao_mensal["criadas"]).round(1)
evolucao_mensal["pct_perdidas"] = (100 * evolucao_mensal["perdidas"] / evolucao_mensal["criadas"]).round(1)

if len(evolucao_mensal) < 2:
    st.caption("Menos de 2 meses no recorte atual -- sem tendência pra mostrar ainda.")
else:
    st.plotly_chart(
        charts.evolucao_funil_mensal(evolucao_mensal), use_container_width=True,
        key=f"{PAGE}_evolucao_mensal", config={"displayModeBar": False},
    )
    st.caption(
        f"⚠️ O mês mais recente ({evolucao_mensal['mes'].max():%b/%Y}) tende a mostrar taxas "
        "mais baixas por natureza: negociações criadas há poucos dias ainda não tiveram tempo "
        "de avançar no funil. Não leia como piora real sem comparar com a maturação dos meses anteriores."
    )

st.divider()

with st.expander("Composição e evolução", expanded=False):
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
    st.caption(
        "Cada linha bucketada pela SUA PRÓPRIA data -- Criadas por quando entraram, "
        "Perdidas por quando foram de fato marcadas como perdidas, Reunião realizada por "
        "quando a negociação de fato chegou lá. Nenhuma das três usa a data de criação como "
        "proxy das outras duas (um lead criado em julho que só teve reunião em setembro "
        "conta em setembro, não em julho). Reunião realizada usa a mesma base do card lá em "
        "cima -- inclui reuniões do período de negociações criadas antes dele; Criadas e "
        "Perdidas consideram só as negociações criadas no período."
    )

    def _bucket(df: pd.DataFrame, col: str) -> pd.Series:
        periodo_pandas = {"D": "D", "W-MON": "W-MON", "MS": "M"}[gran_regra]
        return df[col].dt.tz_convert(None).dt.to_period(periodo_pandas).dt.start_time

    criadas = deals.assign(bucket=_bucket(deals, "deal_created_at")).groupby("bucket")["deal_id"].nunique().reset_index(name="Criadas")
    _lost = deals[deals["deal_status"] == "lost"]
    perdidas_ts = (
        _lost.assign(bucket=_bucket(_lost, "closed_at"))
        .groupby("bucket")["deal_id"].nunique().reset_index(name="Perdidas")
    )
    ganhos = (
        reunioes_periodo.assign(bucket=_bucket(reunioes_periodo, "sdr_ganhou_at"))
        .groupby("bucket")["deal_id"].nunique().reset_index(name="Reunião realizada")
    )
    evol = criadas.merge(perdidas_ts, on="bucket", how="outer").merge(ganhos, on="bucket", how="outer")
    evol = evol.fillna(0).sort_values("bucket")

    fig_evol = charts.serie_temporal(
        evol, x="bucket",
        series={"Criadas": BRAND_BLUE_600, "Perdidas": STATUS_CRITICAL, "Reunião realizada": STATUS_GOOD},
        area=True, altura=340,
    )
    st.plotly_chart(fig_evol, use_container_width=True, key=f"{PAGE}_evolucao")

    st.divider()

with st.expander("Velocidade e gargalos", expanded=False):
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

with st.expander("Perdas e negociações", expanded=False):
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

    # Mesma definicao do card "Tempo mediano no funil" (relogio a partir de Em
    # Prospeccao) -- vazio pra quem ainda nao comecou a contar, nao zero.
    cols_detalhe = {
        "deal_name": "Negociação", "stage_name": "Etapa atual", "deal_status_label": "Status",
        "sdr_name": "SDR", "origem": "Origem", "cidade": "Cidade", "uf": "UF",
        "atividades": "Atividades", "dias_desde_prospeccao": "Dias desde Em Prospecção",
        "motivo_perda": "Motivo de perda",
    }
    tabela = deals[list(cols_detalhe)].copy()
    tabela["dias_desde_prospeccao"] = tabela["dias_desde_prospeccao"].round(0)
    tabela = tabela.rename(columns=cols_detalhe).sort_values("Dias desde Em Prospecção", ascending=False, na_position="last")

    st.dataframe(tabela, use_container_width=True, hide_index=True, height=420)

    st.download_button(
        "⬇️ Baixar recorte em CSV",
        tabela.to_csv(index=False).encode("utf-8-sig"),
        file_name="funil_maquina_isp.csv",
        mime="text/csv",
    )
