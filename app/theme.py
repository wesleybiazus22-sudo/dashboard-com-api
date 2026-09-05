"""Paleta compartilhada (validada para daltonismo) usada em todos os graficos do
dashboard. Ordem categorica fixa -- nunca ciclar, nunca reatribuir por rank.

Cores/fontes ancoradas na identidade da marca (Develcode Design System,
claude.ai/design, projeto "Develcode Design System" em tokens/colors.css e
tokens/typography.css) -- ver `inject_brand()` no fim do arquivo pra aplicar
fonte/cor/raio ao chrome do Streamlit."""

from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------- Marca (Develcode)
BRAND_BLUE_950 = "#000E3D"
BRAND_BLUE_900 = "#001AE1"
BRAND_BLUE_800 = "#0035E2"
BRAND_BLUE_700 = "#0044E6"
BRAND_BLUE_600 = "#0057EA"  # azul da logo -- primaria
BRAND_BLUE_500 = "#026BF0"  # accent/interativo
BRAND_BLUE_400 = "#0F7FFB"
BRAND_BLUE_300 = "#5AA7FF"
BRAND_BLUE_200 = "#A8CCFF"
BRAND_BLUE_100 = "#D6E5FF"
BRAND_BLUE_050 = "#EDF3FF"

BRAND_INK_900 = "#0A0B0D"
BRAND_INK_600 = "#4A4E55"
BRAND_INK_500 = "#6C7178"
BRAND_INK_200 = "#E1E2E6"
BRAND_INK_050 = "#F7F7F9"

BRAND_FONT = "\"Satoshi\", \"Montserrat\", system-ui, -apple-system, \"Segoe UI\", sans-serif"
BRAND_RADIUS_SM = "8px"
BRAND_RADIUS_MD = "12px"
BRAND_RADIUS_LG = "16px"

# Categorica (identidade), ordem fixa -- azul primario reancorado na marca; os
# demais tons mantem o afastamento perceptual original (validado p/ daltonismo).
CAT_BLUE = BRAND_BLUE_600
CAT_ORANGE = "#eb6834"
CAT_AQUA = "#1baf7a"
CAT_YELLOW = "#eda100"
CAT_MAGENTA = "#e87ba4"
CAT_GREEN = "#008300"
CAT_VIOLET = "#4a3aa7"
CAT_RED = "#e34948"

CATEGORICAL = [CAT_BLUE, CAT_ORANGE, CAT_AQUA, CAT_YELLOW, CAT_MAGENTA, CAT_GREEN, CAT_VIOLET, CAT_RED]

# Sequencial azul (magnitude/ordinal) -- direto da rampa de azul da marca
SEQUENTIAL_BLUE = [
    BRAND_BLUE_200,
    BRAND_BLUE_300,
    BRAND_BLUE_400,
    BRAND_BLUE_500,
    BRAND_BLUE_600,
    BRAND_BLUE_700,
    BRAND_BLUE_800,
]

# Status (fixo, nunca reusado como serie) -- da paleta semantica da marca
STATUS_GOOD = "#10B57F"
STATUS_WARNING = "#F5A524"
STATUS_SERIOUS = "#ec835a"
STATUS_CRITICAL = "#E5484D"
STATUS_NEUTRAL = BRAND_INK_500  # muted ink -- para estados "sem retorno" (nem bom nem ruim)

# Chrome / ink
TEXT_PRIMARY = BRAND_INK_900
TEXT_SECONDARY = BRAND_INK_600
TEXT_MUTED = BRAND_INK_500
GRIDLINE = BRAND_INK_200
CHART_SURFACE = "#FFFFFF"

MV_STATUS_COLORS = {
    "Conectado": STATUS_GOOD,
    "Tentando Contato": STATUS_WARNING,
    "Sem Retorno": STATUS_NEUTRAL,
}

# Funil canonico, ordem oficial (usado em todo o dashboard)
CANONICAL_STAGE_ORDER = ["LEAD", "MQL", "SQL", "OPPORTUNITY", "DISCOVERY", "PROPOSAL", "NEGOTIATION"]
CANONICAL_STAGE_LABELS = {
    "LEAD": "Lead",
    "MQL": "MQL",
    "SQL": "SQL",
    "OPPORTUNITY": "Oportunidade",
    "DISCOVERY": "Reunião Realizada",
    "PROPOSAL": "Proposta",
    "NEGOTIATION": "Negociação/Freemium",
}


# ---------------------------------------------------------------- Dimensoes analiticas
# Cores por regiao do Brasil -- fixas, pra que a mesma regiao tenha a mesma cor no
# mapa, na barra e na tabela. Nunca reatribuir por rank.
REGIAO_COLORS = {
    "Norte": CAT_AQUA,
    "Nordeste": CAT_ORANGE,
    "Centro-Oeste": CAT_YELLOW,
    "Sudeste": CAT_BLUE,
    "Sul": CAT_VIOLET,
    "Não informada": BRAND_INK_200,
}

# Cores por origem da negociacao. "Melhor Venda" fica com o azul da marca por ser o
# canal proprio; os demais recebem tons distintos e estaveis.
ORIGEM_COLORS = {
    "Melhor Venda": BRAND_BLUE_600,
    "Feiras e Eventos": CAT_ORANGE,
    "Prospecção Ativa": CAT_AQUA,
    "Indicação por Clientes": CAT_GREEN,
    "Indicação por Parceiros": CAT_VIOLET,
    "Origem não informada": BRAND_INK_200,
}

DEAL_STATUS_COLORS = {
    "ongoing": BRAND_BLUE_500,
    "won": STATUS_GOOD,
    "lost": STATUS_CRITICAL,
}
DEAL_STATUS_LABELS = {"ongoing": "Em andamento", "won": "Ganha", "lost": "Perdida"}

# Objetivo de campanha do Meta Ads -- os valores brutos vem em ingles/caixa alta
# (formato interno da API), sem tradução amigavel pro dashboard.
META_OBJECTIVE_LABELS = {
    "OUTCOME_LEADS": "Geração de leads",
    "OUTCOME_ENGAGEMENT": "Engajamento",
    "OUTCOME_TRAFFIC": "Tráfego",
    "OUTCOME_AWARENESS": "Reconhecimento",
    "OUTCOME_SALES": "Vendas",
    "OUTCOME_APP_PROMOTION": "Promoção de app",
}

META_STATUS_COLORS = {
    "ACTIVE": STATUS_GOOD,
    "PAUSED": STATUS_NEUTRAL,
    "ARCHIVED": BRAND_INK_200,
    "DELETED": BRAND_INK_200,
}
META_STATUS_LABELS = {
    "ACTIVE": "Ativa", "PAUSED": "Pausada", "ARCHIVED": "Arquivada", "DELETED": "Excluída",
}

UF_NOMES = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia",
    "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás",
    "MA": "Maranhão", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul",
    "MG": "Minas Gerais", "PA": "Pará", "PB": "Paraíba", "PR": "Paraná",
    "PE": "Pernambuco", "PI": "Piauí", "RJ": "Rio de Janeiro",
    "RN": "Rio Grande do Norte", "RS": "Rio Grande do Sul", "RO": "Rondônia",
    "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo", "SE": "Sergipe",
    "TO": "Tocantins",
}


def escala_azul_marca() -> list:
    """Escala continua da rampa de azul da marca, pro mapa e heatmaps. Comeca num
    tom bem claro (nao branco puro) pra que UF com volume 1 ainda seja visivel
    contra o fundo do mapa."""
    return [
        [0.0, BRAND_BLUE_050],
        [0.15, BRAND_BLUE_100],
        [0.35, BRAND_BLUE_300],
        [0.6, BRAND_BLUE_500],
        [0.8, BRAND_BLUE_700],
        [1.0, BRAND_BLUE_950],
    ]


def format_int(n) -> str:
    """Inteiro com separador de milhar no padrao BR (1.234)."""
    try:
        return f"{int(n):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


def format_pct(v, casas: int = 1) -> str:
    if v is None or v != v:
        return "—"
    return f"{v:.{casas}f}%".replace(".", ",")


def format_money(v, moeda: str = "R$") -> str:
    """Valor monetario no padrao BR: milhar com ponto, decimal com virgula."""
    if v is None or v != v:
        return "—"
    inteiro, _, centavos = f"{float(v):,.2f}".partition(".")
    inteiro = inteiro.replace(",", ".")
    return f"{moeda} {inteiro},{centavos}"


def format_days(hours, casas: int = 1) -> str:
    """Converte horas em dias (1 casa decimal por padrao, virgula BR). Usado nas
    metricas de velocidade -- pedido do usuario pra nao misturar "5d 6h" (formato
    adaptativo de `format_duration`) com um numero direto de dias, mais facil de
    comparar de uma etapa pra outra."""
    if hours is None or hours != hours:
        return "—"
    dias = hours / 24
    return f"{dias:.{casas}f} dias".replace(".", ",")


def format_duration(hours: float | None) -> str:
    """Formata uma duracao em horas (float) pra texto legivel, escolhendo a unidade
    pela magnitude: minutos abaixo de 1h, horas+minutos abaixo de 1 dia, dias+horas
    acima disso. Evita mostrar "0.0 dias" pra etapas que na pratica duram minutos."""
    if hours is None or hours != hours:  # NaN
        return "—"
    if hours < 0:
        hours = 0

    total_minutes = round(hours * 60)
    if total_minutes < 60:
        return f"{total_minutes} min"

    if hours < 24:
        h = int(hours)
        m = round((hours - h) * 60)
        return f"{h}h {m}min" if m else f"{h}h"

    days = int(hours // 24)
    rem_h = round(hours % 24)
    if rem_h == 24:  # arredondamento pode empurrar pro proximo dia
        days += 1
        rem_h = 0
    return f"{days}d {rem_h}h" if rem_h else f"{days}d"


def base_layout(fig, height: int = 420):
    """Aplica chrome consistente (grid recessivo, fundo, fonte) a uma figura Plotly."""
    fig.update_layout(
        height=height,
        plot_bgcolor=CHART_SURFACE,
        paper_bgcolor=CHART_SURFACE,
        font=dict(color=TEXT_PRIMARY, family=BRAND_FONT),
        margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    fig.update_xaxes(gridcolor=GRIDLINE, zerolinecolor=GRIDLINE)
    fig.update_yaxes(gridcolor=GRIDLINE, zerolinecolor=GRIDLINE)
    return fig


# ---------------------------------------------------------------- Marca: CSS + logo
_LOGO_DIR = Path(__file__).resolve().parent / "assets" / "logos"


def inject_brand() -> None:
    """Injeta fonte (Satoshi via Fontshare + Montserrat via Google Fonts), cor
    primaria e raio de borda da marca no chrome do Streamlit. Chamar uma vez no
    topo de cada pagina, logo apos `st.set_page_config`."""
    st.markdown(
        f"""
        <style>
        @import url('https://api.fontshare.com/v2/css?f[]=satoshi@400,500,700,900&display=swap');
        @import url('https://fonts.googleapis.com/css2?family=Montserrat:ital,wght@0,300..800;1,300..800&display=swap');

        html, body, [class*="css"] {{
            font-family: {BRAND_FONT};
        }}

        h1, h2, h3, h4, h5, h6 {{
            font-family: {BRAND_FONT};
            font-weight: 700;
            color: {BRAND_INK_900};
        }}

        /* Botoes, sliders e outros controles primarios ja seguem primaryColor via
        .streamlit/config.toml -- aqui so arredondamos o chrome pra bater com os
        tokens de radius da marca (cards 12-16px, controles 8px). */
        div[data-testid="stMetric"],
        div[data-testid="stDataFrame"],
        div[data-testid="stVerticalBlockBorderWrapper"],
        .stAlert {{
            border-radius: {BRAND_RADIUS_MD};
        }}

        .stButton > button, .stDownloadButton > button {{
            border-radius: {BRAND_RADIUS_SM};
            font-family: {BRAND_FONT};
            font-weight: 500;
        }}

        section[data-testid="stSidebar"] {{
            background-color: {BRAND_INK_050};
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_brand_header(title: str, subtitle: str | None = None) -> None:
    """Cabecalho de pagina com a logo da Develcode + titulo, no lugar de st.title puro."""
    logo_path = _LOGO_DIR / "develcode-horizontal-black.png"
    if logo_path.exists():
        st.image(str(logo_path), width=180)
    st.title(title)
    if subtitle:
        st.caption(subtitle)
