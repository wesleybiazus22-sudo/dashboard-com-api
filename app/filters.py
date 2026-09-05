"""Controles de filtro compartilhados por todas as paginas.

Centralizado de proposito: quando "Últimos 30 dias" significa a mesma coisa em toda
pagina, os numeros de paginas diferentes passam a ser comparaveis entre si. Cada
pagina montando o proprio filtro de data e a forma mais rapida de um dashboard
comecar a se contradizer.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

PRESETS = [
    "Tudo",
    "Últimos 7 dias",
    "Últimos 30 dias",
    "Últimos 90 dias",
    "Este mês",
    "Mês passado",
    "Desde agosto",
    "Personalizado",
]

GRANULARIDADES = {"Dia": "D", "Semana": "W-MON", "Mês": "MS"}


def _as_date(v):
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, pd.Timestamp):
        return v
    ts = pd.Timestamp(v)
    return None if pd.isna(ts) else ts.date()


def intervalo_do_preset(preset: str, minimo: date, maximo: date) -> tuple[date, date]:
    """Traduz o preset em (inicio, fim), sempre recortado ao que existe na base.

    O recorte importa: sem ele, "Últimos 7 dias" numa base cuja ultima carga foi ha
    duas semanas devolve um periodo vazio e a pagina inteira aparece zerada, o que
    parece bug de dados quando na verdade e so o recorte."""
    hoje = date.today()
    if preset == "Últimos 7 dias":
        ini, fim = hoje - timedelta(days=6), hoje
    elif preset == "Últimos 30 dias":
        ini, fim = hoje - timedelta(days=29), hoje
    elif preset == "Últimos 90 dias":
        ini, fim = hoje - timedelta(days=89), hoje
    elif preset == "Este mês":
        ini, fim = hoje.replace(day=1), hoje
    elif preset == "Mês passado":
        primeiro = hoje.replace(day=1)
        fim = primeiro - timedelta(days=1)
        ini = fim.replace(day=1)
    elif preset == "Desde agosto":
        # Agosto do ano corrente -- se hoje ainda nao chegou em agosto (ex: em
        # marco), usa o agosto do ano anterior, senao o preset ficaria "no futuro"
        # e devolveria um periodo vazio.
        ini = date(hoje.year, 8, 1)
        if ini > hoje:
            ini = date(hoje.year - 1, 8, 1)
        fim = hoje
    else:  # "Tudo"
        return minimo, maximo

    return max(ini, minimo), min(fim, maximo)


def filtro_periodo(
    minimo: date,
    maximo: date,
    *,
    page: str,
    label: str = "Período",
    default: str = "Tudo",
) -> tuple[date, date, str]:
    """Filtro de data com presets + ajuste fino. Devolve (inicio, fim, preset)."""
    minimo, maximo = _as_date(minimo), _as_date(maximo)
    if minimo is None or maximo is None:
        hoje = date.today()
        return hoje, hoje, "Tudo"
    if minimo > maximo:
        minimo, maximo = maximo, minimo

    st.markdown(f"**{label}**")
    preset = st.radio(
        label,
        PRESETS,
        index=PRESETS.index(default),
        horizontal=True,
        key=f"preset::{page}",
        label_visibility="collapsed",
    )

    if preset == "Personalizado":
        if minimo == maximo:
            st.caption(f"Base tem um único dia: {minimo.strftime('%d/%m/%Y')}.")
            return minimo, maximo, preset
        ini, fim = st.slider(
            "Arraste para ajustar",
            min_value=minimo,
            max_value=maximo,
            value=(minimo, maximo),
            format="DD/MM/YYYY",
            key=f"slider::{page}",
            label_visibility="collapsed",
        )
        return ini, fim, preset

    ini, fim = intervalo_do_preset(preset, minimo, maximo)
    if ini > fim:
        # o preset caiu inteiramente fora da janela de dados existente
        st.warning(
            f"Não há dados em «{preset}». A base vai de {minimo.strftime('%d/%m/%Y')} "
            f"a {maximo.strftime('%d/%m/%Y')}."
        )
        return maximo, maximo, preset

    st.caption(f"📅 {ini.strftime('%d/%m/%Y')} — {fim.strftime('%d/%m/%Y')}")
    return ini, fim, preset


def filtro_granularidade(page: str, default: str = "Semana") -> tuple[str, str]:
    """Granularidade das series temporais. Devolve (rotulo, regra_pandas)."""
    opcoes = list(GRANULARIDADES)
    rotulo = st.radio(
        "Agrupar por",
        opcoes,
        index=opcoes.index(default),
        horizontal=True,
        key=f"gran::{page}",
    )
    return rotulo, GRANULARIDADES[rotulo]


def recorta_periodo(df: pd.DataFrame, coluna: str, inicio: date, fim: date) -> pd.DataFrame:
    """Recorta um DataFrame por uma coluna de data/hora, comparando apenas a DATA.

    Comparar direto contra o timestamp perderia o ultimo dia do intervalo: um evento
    as 16h de 21/08 e maior que o limite 21/08 00:00."""
    if df is None or df.empty or coluna not in df.columns:
        return df
    dias = pd.to_datetime(df[coluna], errors="coerce", utc=True).dt.tz_localize(None).dt.date
    return df[dias.notna() & (dias >= inicio) & (dias <= fim)]


def multiselect_dim(
    df: pd.DataFrame,
    coluna: str,
    label: str,
    *,
    page: str,
    ordenar_por_volume: bool = True,
) -> list[str]:
    """Multiselect populado a partir dos valores presentes no recorte atual.

    Popular a partir do recorte (e nao da base inteira) evita oferecer opcoes que
    resultariam em tela vazia -- se nenhuma negociacao de PE sobrou no periodo, "PE"
    nao deve nem aparecer na lista."""
    if df is None or df.empty or coluna not in df.columns:
        return []
    serie = df[coluna].dropna().astype(str)
    if serie.empty:
        return []
    if ordenar_por_volume:
        opcoes = serie.value_counts().index.tolist()
    else:
        opcoes = sorted(serie.unique())
    return st.multiselect(label, opcoes, default=[], key=f"ms::{page}::{coluna}")


def aplica_multiselects(df: pd.DataFrame, selecoes: dict[str, list[str]]) -> pd.DataFrame:
    out = df
    for coluna, valores in selecoes.items():
        if valores and coluna in out.columns:
            out = out[out[coluna].astype(str).isin(valores)]
    return out
