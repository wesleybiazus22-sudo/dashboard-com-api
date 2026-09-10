"""Componentes de grafico reutilizaveis, ja no visual da marca e ja preparados para
o drill cruzado (`app.interact`).

Todo grafico clicavel carrega o valor canonico da dimensao em `customdata`, que e o
que `interact._value` le -- assim o filtro nunca depende do rotulo exibido, que pode
estar truncado, formatado ou traduzido.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.theme import (
    BRAND_BLUE_050,
    BRAND_BLUE_600,
    BRAND_INK_200,
    BRAND_INK_500,
    CAT_ORANGE,
    CATEGORICAL,
    GRIDLINE,
    STATUS_CRITICAL,
    STATUS_GOOD,
    STATUS_SERIOUS,
    STATUS_WARNING,
    TEXT_MUTED,
    UF_NOMES,
    base_layout,
    escala_azul_marca,
    format_int,
    format_pct,
)

_GEOJSON = Path(__file__).resolve().parent / "assets" / "geo" / "br_states.geojson"

# Estilo de selecao NATIVO do Plotly: o item clicado fica opaco e os demais esmaecem,
# sem que a figura precise ser reconstruida em Python. E o que permite destacar o
# item selecionado mantendo a spec do grafico identica entre reruns -- requisito pro
# Streamlit preservar a selecao (ver docstring de `barra_dimensao`).
_DESTAQUE = {
    "selected": dict(marker=dict(opacity=1.0)),
    "unselected": dict(marker=dict(opacity=0.28)),
}


@lru_cache(maxsize=1)
def geojson_brasil() -> dict | None:
    """GeoJSON dos estados, servido de um arquivo LOCAL de proposito.

    Buscar de uma CDN a cada carga tornaria o mapa refem de rede e quebraria o
    dashboard offline; o arquivo ja vem simplificado (2 casas decimais, ~870 KB),
    precisao mais que suficiente para um mapa nacional."""
    if not _GEOJSON.exists():
        return None
    with _GEOJSON.open(encoding="utf-8") as fh:
        return json.load(fh)


def cor_conversao(pct: float | None) -> str:
    """Semaforo de taxa de conversao. Limiares calibrados para funil outbound B2B,
    onde uma passagem de etapa abaixo de 25% ja e gargalo real."""
    if pct is None or pct != pct:
        return BRAND_INK_200
    if pct >= 60:
        return STATUS_GOOD
    if pct >= 40:
        return STATUS_WARNING
    if pct >= 20:
        return STATUS_SERIOUS
    return STATUS_CRITICAL


# ---------------------------------------------------------------- Funil
def funil(df: pd.DataFrame, *, altura: int = 460) -> go.Figure:
    """Alcance em escala linear; taxas em coluna alinhada, sem semáforo arbitrário."""
    d = df.sort_values("passo").copy()
    fig = go.Figure()
    if d.empty:
        fig.add_annotation(text="Sem alcance no recorte", showarrow=False)
        return base_layout(fig, height=altura)
    total = max(float(d["negociacoes"].max()), 1)
    fig.add_trace(go.Bar(
        y=d["etapa"], x=d["negociacoes"], orientation="h",
        marker=dict(color=BRAND_BLUE_600),
        text=[format_int(v) for v in d["negociacoes"]],
        textposition="outside", cliponaxis=False,
        customdata=[[r["etapa"], format_pct(r.get("conversao_etapa_pct")),
                     format_pct(r.get("retencao_topo_pct"))] for _, r in d.iterrows()],
        hovertemplate="<b>%{y}</b><br>%{x} negociações alcançaram a etapa"
                      "<br>Da anterior: %{customdata[1]}<br>Do topo: %{customdata[2]}<extra></extra>",
    ))
    for i, (_, row) in enumerate(d.iterrows()):
        fig.add_annotation(x=1, xref="paper", y=row["etapa"], yref="y",
            text="Base" if i == 0 else format_pct(row.get("conversao_etapa_pct")),
            showarrow=False, xanchor="right", font=dict(size=13, color=TEXT_MUTED))
    fig.add_annotation(x=1, xref="paper", y=1.08, yref="paper", text="Da anterior",
                       showarrow=False, xanchor="right", font=dict(size=11, color=TEXT_MUTED))
    base_layout(fig, height=altura)
    fig.update_xaxes(showgrid=False, showticklabels=False, range=[0, total * 1.55])
    fig.update_yaxes(showgrid=False, autorange="reversed")
    fig.update_layout(showlegend=False, margin=dict(l=10, r=18, t=42, b=15), bargap=0.42)
    return fig


def snapshot_estagios(df: pd.DataFrame, *, altura: int = 460) -> go.Figure:
    """Foto do momento: quantas negociacoes estao PARADAS em cada etapa agora,
    incluindo terminais (No-show, Encerrado/Standby, Desistiu) -- diferente de
    `funil()`, que e cumulativo/historico (quem ja alcancou aquela etapa ou
    alguma depois). Sem % de conversao entre etapas -- nao faz sentido aqui,
    porque uma etapa com poucas negociacoes pode simplesmente ser rapida de
    passar, nao um gargalo."""
    d = df.sort_values("passo").copy()
    fig = go.Figure()
    if d.empty:
        fig.add_annotation(text="Sem negociações no recorte", showarrow=False)
        return base_layout(fig, height=altura)
    total = max(float(d["negociacoes"].max()), 1)
    fig.add_trace(go.Bar(
        y=d["etapa"], x=d["negociacoes"], orientation="h",
        marker=dict(color=CAT_ORANGE),
        text=[format_int(v) for v in d["negociacoes"]],
        textposition="outside", cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>%{x} negociações aqui agora<extra></extra>",
    ))
    base_layout(fig, height=altura)
    fig.update_xaxes(showgrid=False, showticklabels=False, range=[0, total * 1.25])
    fig.update_yaxes(showgrid=False, autorange="reversed")
    fig.update_layout(showlegend=False, margin=dict(l=10, r=18, t=20, b=15), bargap=0.42)
    return fig


def queda_entre_etapas(df: pd.DataFrame, *, altura: int = 340) -> go.Figure:
    """Quantas negociacoes se perdem em cada passagem de etapa, em valor absoluto.

    Complementa o funil: o funil mostra a taxa, este mostra o TAMANHO do vazamento.
    Uma etapa com 15% de conversao que so recebe 20 negociacoes importa muito menos
    que uma com 75% que recebe 240 -- e so este grafico deixa isso obvio."""
    # o rotulo precisa ser montado ANTES de filtrar: `shift(1)` sobre as linhas ja
    # filtradas pegaria a etapa anterior *sobrevivente*, e nao a etapa realmente
    # anterior no funil, inventando movimentos que nunca existiram
    d = df.sort_values("passo").copy()
    d["movimento"] = d["etapa"].shift(1) + " → " + d["etapa"]
    d = d[(d["perdidas_no_passo"] > 0) & d["movimento"].notna()]
    if d.empty:
        return None

    fig = go.Figure(
        go.Bar(
            y=d["movimento"],
            x=d["perdidas_no_passo"],
            orientation="h",
            marker=dict(color="#D68A35"),
            text=[f"  {format_int(v)}" for v in d["perdidas_no_passo"]],
            textposition="outside",
            cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>%{x} negociações não avançaram<extra></extra>",
        )
    )
    fig.update_xaxes(showgrid=False, showticklabels=False, range=[0, d["perdidas_no_passo"].max() * 1.35])
    fig.update_yaxes(autorange="reversed")
    base_layout(fig, height=altura)
    fig.update_layout(margin=dict(l=10, r=10, t=20, b=10), showlegend=False)
    return fig


# ---------------------------------------------------------------- Mapa
def mapa_uf(
    dados: pd.DataFrame,
    *,
    coluna_valor: str = "negociacoes",
    titulo_valor: str = "negociações",
    altura: int = 520,
) -> go.Figure | None:
    """Coropleto do Brasil por UF. `dados` precisa ter as colunas `uf` e `coluna_valor`."""
    gj = geojson_brasil()
    if gj is None or dados.empty:
        return None

    d = dados.dropna(subset=["uf"]).copy()
    if d.empty:
        return None
    d["nome_uf"] = d["uf"].map(UF_NOMES).fillna(d["uf"])

    fig = go.Figure(
        go.Choropleth(
            geojson=gj,
            locations=d["uf"],
            z=d[coluna_valor],
            featureidkey="properties.sigla",
            colorscale=escala_azul_marca(),
            marker_line_color="#FFFFFF",
            marker_line_width=0.8,
            customdata=d[["uf", "nome_uf"]].values,
            selected=_DESTAQUE["selected"], unselected=_DESTAQUE["unselected"],
            hovertemplate="<b>%{customdata[1]}</b><br>%{z} " + titulo_valor + "<extra></extra>",
            colorbar=dict(title=dict(text=titulo_valor, side="right"), thickness=12, len=0.7, outlinewidth=0),
        )
    )
    # fitbounds recorta no que existe no geojson (o Brasil inteiro), evitando que o
    # mapa apareca perdido no meio do oceano quando so ha 2-3 UFs com dado
    fig.update_geos(
        fitbounds="locations",
        visible=False,
        bgcolor="rgba(0,0,0,0)",
        showframe=False,
        showcoastlines=False,
    )
    fig.update_layout(
        height=altura,
        margin=dict(l=0, r=0, t=10, b=0),
        paper_bgcolor="#FFFFFF",
        geo=dict(bgcolor="rgba(0,0,0,0)"),
    )
    return fig


# ---------------------------------------------------------------- Barras clicaveis
def barra_dimensao(
    dados: pd.DataFrame,
    *,
    dimensao: str,
    valor: str,
    cores: dict[str, str] | None = None,
    cor_padrao: str = BRAND_BLUE_600,
    horizontal: bool = True,
    altura: int = 320,
    sufixo: str = "",
    maximo_itens: int = 15,
) -> go.Figure:
    """Barra por dimensao categorica, clicavel.

    NAO recebe "quais itens estao selecionados" de proposito. O destaque do item
    clicado vem do estado de selecao NATIVO do Plotly (ver `_DESTAQUE`), nunca de
    recolorir a figura em Python: o Streamlit descarta a selecao de um grafico cuja
    spec mudou, entao pintar a barra selecionada de outra cor apagaria a propria
    selecao que causou a pintura -- o filtro se desfazia sozinho na rerun seguinte."""
    d = dados.head(maximo_itens).copy()
    rotulos = d[dimensao].astype(str).tolist()

    if cores:
        base = [cores.get(r, cor_padrao) for r in rotulos]
    else:
        base = [cor_padrao] * len(rotulos)

    textos = [f"{format_int(v)}{sufixo}" for v in d[valor]]

    if horizontal:
        fig = go.Figure(
            go.Bar(
                y=rotulos, x=d[valor], orientation="h",
                marker=dict(color=base), customdata=d[[dimensao]].values,
                selected=_DESTAQUE["selected"], unselected=_DESTAQUE["unselected"],
                text=textos, textposition="outside", cliponaxis=False,
                hovertemplate="<b>%{y}</b><br>%{x}" + sufixo + "<extra></extra>",
            )
        )
        fig.update_yaxes(autorange="reversed", showgrid=False)
        fig.update_xaxes(showgrid=False, showticklabels=False, range=[0, max(d[valor].max(), 1) * 1.25])
    else:
        fig = go.Figure(
            go.Bar(
                x=rotulos, y=d[valor],
                marker=dict(color=base), customdata=d[[dimensao]].values,
                selected=_DESTAQUE["selected"], unselected=_DESTAQUE["unselected"],
                text=textos, textposition="outside", cliponaxis=False,
                hovertemplate="<b>%{x}</b><br>%{y}" + sufixo + "<extra></extra>",
            )
        )
        fig.update_xaxes(showgrid=False)
        fig.update_yaxes(showgrid=True, rangemode="tozero")

    base_layout(fig, height=altura)
    fig.update_layout(showlegend=False, margin=dict(l=10, r=10, t=20, b=10))
    return fig


def rosca(
    dados: pd.DataFrame,
    *,
    dimensao: str,
    valor: str,
    cores: dict[str, str] | None = None,
    altura: int = 320,
    centro: str = "",
) -> go.Figure:
    """Rosca clicavel. Usada só onde a pergunta é de COMPOSIÇÃO (como o mix se divide),
    nunca para comparar magnitudes -- para isso, barra."""
    d = dados.copy()
    rotulos = d[dimensao].astype(str).tolist()
    paleta = [cores.get(r, CATEGORICAL[i % len(CATEGORICAL)]) for i, r in enumerate(rotulos)] if cores else [
        CATEGORICAL[i % len(CATEGORICAL)] for i in range(len(rotulos))
    ]
    fig = go.Figure(
        go.Pie(
            labels=rotulos, values=d[valor], hole=0.62,
            marker=dict(colors=paleta, line=dict(color="#FFFFFF", width=2)),
            customdata=d[[dimensao]].values, sort=False, direction="clockwise",
            textinfo="percent", textposition="inside",
            hovertemplate="<b>%{label}</b><br>%{value} (%{percent})<extra></extra>",
        )
    )
    if centro:
        fig.add_annotation(text=centro, showarrow=False, font=dict(size=15, color=TEXT_MUTED))
    base_layout(fig, height=altura)
    fig.update_layout(margin=dict(l=10, r=10, t=20, b=10))
    return fig


def serie_temporal(
    dados: pd.DataFrame,
    *,
    x: str,
    series: dict[str, str],
    altura: int = 340,
    area: bool = False,
    sufixo_y: str = "",
) -> go.Figure:
    """Linha/area temporal. `series` mapeia coluna -> cor."""
    fig = go.Figure()
    for i, (coluna, cor) in enumerate(series.items()):
        if coluna not in dados.columns:
            continue
        fig.add_trace(
            go.Scatter(
                x=dados[x], y=dados[coluna], name=coluna, mode="lines+markers",
                line=dict(color=cor, width=2.5, shape="linear"),
                marker=dict(size=7, color=cor),
                fill="tozeroy" if area and i == 0 else None,
                fillcolor=BRAND_BLUE_050 if area and i == 0 else None,
                hovertemplate="<b>%{x|%d/%m/%Y}</b><br>" + coluna + ": %{y}" + sufixo_y + "<extra></extra>",
            )
        )
    fig.update_yaxes(rangemode="tozero", ticksuffix=sufixo_y)
    fig.update_xaxes(showgrid=False)
    base_layout(fig, height=altura)
    fig.update_layout(hovermode="x unified")
    return fig


# ---------------------------------------------------------------- KPIs
def kpi(col, rotulo: str, valor: str, delta: str | None = None, ajuda: str | None = None) -> None:
    col.metric(rotulo, valor, delta, help=ajuda)


def selecionavel(fig, *, key: str):
    """`st.plotly_chart` no modo clique-para-filtrar.

    O `dragmode="select"` NAO e cosmetico -- e o que faz o clique funcionar. O
    Streamlit nao escuta `plotly_click`: ele so registra handler para
    `plotly_selected`/`plotly_deselect`. Com o dragmode padrao ("pan"/"zoom"), um
    clique simples numa barra nao gera selecao nenhuma e o grafico fica inerte por
    mais que `on_select="rerun"` esteja ligado. Em modo "select", o Plotly trata um
    clique sem arrasto como selecao do ponto sob o cursor e emite `plotly_selected`,
    que e justamente o evento que o Streamlit devolve pro Python.
    """
    fig.update_layout(dragmode="select", clickmode="event+select")
    return st.plotly_chart(
        fig,
        use_container_width=True,
        key=key,
        on_select="rerun",
        selection_mode="points",
        config={"displayModeBar": False},
    )
