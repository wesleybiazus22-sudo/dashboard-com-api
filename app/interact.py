"""Drill-down cruzado: clicar em qualquer ponto de um grafico passa a filtrar TODOS
os outros elementos da pagina.

Como funciona
-------------
Cada grafico "clicavel" e renderizado com `on_select="rerun"` e declara qual
DIMENSAO ele representa (origem, uf, sdr, etapa, ...). Quando o usuario clica,
guardamos o valor selecionado em `st.session_state` e todo o resto da pagina passa
a ler os dados ja filtrados por ele.

O detalhe critico -- e a razao deste modulo existir em vez de uma linha solta em
cada pagina -- e que o Streamlit devolve o MESMO evento de selecao em toda rerun,
nao so na rerun em que o clique aconteceu. Se a gente escrevesse a selecao no
estado a cada passada, o filtro nunca poderia ser removido: limpar o drill causa
uma rerun, a rerun re-entrega o evento antigo e o filtro se reaplica sozinho, pra
sempre. Por isso guardamos uma ASSINATURA da ultima selecao vista por grafico e so
reagimos quando ela realmente muda.

O `st.rerun()` no fim de `capture` tambem e proposital: os graficos acima do que
foi clicado ja foram desenhados nesta passada com os dados antigos, entao sem uma
nova passada o clique so afetaria os elementos abaixo dele na pagina.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

# Rotulo amigavel de cada dimensao, usado nos "chips" de filtro ativo.
DIM_LABELS = {
    "origem": "Origem",
    "regiao": "Região",
    "uf": "UF",
    "cidade": "Cidade",
    "sdr_name": "SDR",
    "closer_name": "Closer",
    "stage_name": "Etapa atual",
    "etapa": "Etapa do funil",
    "deal_status": "Status",
    "motivo_perda": "Motivo de perda",
    "campanha": "Campanha",
    "mv_status": "Status MV",
    "campaign_label": "Campanha MV",
    "pipeline_name": "Pipeline",
    "channel_group": "Canal",
    "country": "País",
    "device_category": "Dispositivo",
    "utm_campaign": "Campanha (UTM)",
}


def _bucket(page: str) -> dict:
    """Drills ativos da pagina. Cada pagina tem o proprio conjunto -- filtrar por
    'SP' no mapa nao deve vazar pra pagina do Melhor Venda sem o usuario pedir."""
    key = f"_drill::{page}"
    if key not in st.session_state:
        st.session_state[key] = {}
    return st.session_state[key]


def active(page: str) -> dict:
    return dict(_bucket(page))


def _points(event) -> list:
    """Extrai a lista de pontos do evento de selecao, tolerando as duas formas que o
    Streamlit ja usou (objeto com atributos e dict puro)."""
    if event is None:
        return []
    sel = getattr(event, "selection", None)
    if sel is None and isinstance(event, dict):
        sel = event.get("selection")
    if sel is None:
        return []
    pts = getattr(sel, "points", None)
    if pts is None and isinstance(sel, dict):
        pts = sel.get("points")
    return list(pts or [])


def _value(point) -> str | None:
    """Valor da dimensao dentro de um ponto clicado.

    `customdata` vem primeiro de proposito: e o unico campo que a gente controla, e
    guardar ali o valor canonico evita depender do que o eixo mostra (um rotulo pode
    estar truncado, traduzido, ou ser uma UF exibida como nome completo)."""
    if not isinstance(point, dict):
        point = getattr(point, "__dict__", {}) or {}
    cd = point.get("customdata")
    if isinstance(cd, (list, tuple)) and cd:
        return str(cd[0])
    if isinstance(cd, str) and cd:
        return cd
    for campo in ("label", "x", "location", "y"):
        v = point.get(campo)
        if isinstance(v, str) and v:
            return v
    return None


def capture(event, *, chart_key: str, dim: str, page: str) -> None:
    """Traduz o evento de selecao de um grafico em drill ativo.

    Chamar logo depois do `st.plotly_chart(...)` correspondente."""
    valores = sorted({v for v in (_value(p) for p in _points(event)) if v})
    assinatura = tuple(valores)
    sig_key = f"_sig::{page}::{chart_key}"

    if st.session_state.get(sig_key) == assinatura:
        return  # nada mudou desde a ultima passada -- ver docstring do modulo
    st.session_state[sig_key] = assinatura

    drills = _bucket(page)
    if valores:
        drills[dim] = valores
    else:
        drills.pop(dim, None)
    st.rerun()


def clear(page: str, dim: str | None = None) -> None:
    """Remove um drill (ou todos). Tambem zera as assinaturas dos graficos, senao o
    evento antigo ainda em memoria reaplicaria o filtro na proxima passada."""
    drills = _bucket(page)
    if dim is None:
        drills.clear()
    else:
        drills.pop(dim, None)
    for k in [k for k in st.session_state if k.startswith(f"_sig::{page}::")]:
        st.session_state[k] = ()


def apply(df: pd.DataFrame, page: str, *, ignorar: tuple[str, ...] = ()) -> pd.DataFrame:
    """Aplica todos os drills ativos a um DataFrame.

    `ignorar` serve pro grafico que ORIGINOU o drill: um grafico de origem filtrado
    por origem mostraria uma barra so, escondendo o contexto de onde o usuario
    clicou. Ele continua mostrando todas as barras, so que destacadas."""
    if df is None or df.empty:
        return df
    out = df
    for dim, valores in _bucket(page).items():
        if dim in ignorar or dim not in out.columns:
            continue
        out = out[out[dim].astype(str).isin([str(v) for v in valores])]
    return out


def chips(page: str) -> None:
    """Barra de filtros ativos, com botao pra remover cada um."""
    drills = _bucket(page)
    if not drills:
        st.caption("💡 Clique em qualquer gráfico clicável para filtrar a página inteira por aquele dado.")
        return

    st.markdown("**Detalhando:**")
    cols = st.columns(min(len(drills), 4) + 1)
    for i, (dim, valores) in enumerate(list(drills.items())):
        rotulo = DIM_LABELS.get(dim, dim)
        texto = ", ".join(str(v) for v in valores[:3])
        if len(valores) > 3:
            texto += f" +{len(valores) - 3}"
        with cols[i % len(cols)]:
            if st.button(f"✕  {rotulo}: {texto}", key=f"chip::{page}::{dim}", use_container_width=True):
                clear(page, dim)
                st.rerun()
    with cols[-1]:
        if st.button("Limpar tudo", key=f"chip::{page}::__all__", type="primary", use_container_width=True):
            clear(page)
            st.rerun()


def destaque(valores: list[str], selecionados: list[str] | None, cor: str, cor_apagada: str = "#E1E2E6") -> list[str]:
    """Lista de cores onde os itens NAO selecionados ficam apagados -- da o feedback
    visual de "voce esta olhando este aqui" sem esconder o resto do contexto."""
    if not selecionados:
        return [cor] * len(valores)
    alvo = {str(v) for v in selecionados}
    return [cor if str(v) in alvo else cor_apagada for v in valores]
