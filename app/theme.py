"""Paleta compartilhada (validada para daltonismo) usada em todos os graficos do
dashboard. Ordem categorica fixa -- nunca ciclar, nunca reatribuir por rank.

Cores/fontes ancoradas na identidade da marca (Develcode Design System,
claude.ai/design, projeto "Develcode Design System" em tokens/colors.css e
tokens/typography.css) -- ver `inject_brand()` no fim do arquivo pra aplicar
fonte/cor/raio ao chrome do Streamlit."""

from pathlib import Path
from base64 import b64encode
from html import escape
from functools import lru_cache

import streamlit as st

# ---------------------------------------------------------------- Marca (Develcode)
BRAND_BLUE_950 = "#08142A"
BRAND_BLUE_900 = "#001AE1"
BRAND_BLUE_800 = "#0035E2"
BRAND_BLUE_700 = "#0044E6"
BRAND_BLUE_600 = "#006FFF"  # azul da logo -- primaria
BRAND_BLUE_500 = "#1179FF"  # accent/interativo
BRAND_BLUE_400 = "#01C8FF"
BRAND_BLUE_300 = "#5AA7FF"
BRAND_BLUE_200 = "#A8CCFF"
BRAND_BLUE_100 = "#D6E5FF"
BRAND_BLUE_050 = "#EDF3FF"

BRAND_INK_900 = "#0A0B0D"
BRAND_INK_600 = "#4A4E55"
BRAND_INK_500 = "#6C7178"
BRAND_INK_200 = "#E1E2E6"
BRAND_INK_050 = "#F7F7F9"

BRAND_FONT = '"Encode Sans", "Segoe UI", Arial, sans-serif'
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
        font=dict(color=TEXT_SECONDARY, family=BRAND_FONT, size=13),
        separators=",.",
        hoverlabel=dict(bgcolor="#FFFFFF", font_size=13),
        bargap=0.3,
        margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    fig.update_xaxes(gridcolor=GRIDLINE, zerolinecolor=GRIDLINE)
    fig.update_yaxes(gridcolor="#EDF0F5", zerolinecolor=GRIDLINE, automargin=True)
    fig.update_xaxes(automargin=True)
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
        @import url('https://fonts.googleapis.com/css2?family=Encode+Sans:wght@400;500;600;700&display=swap');
        @font-face {{ font-family: 'ISP Virtual'; src: url(data:font/otf;base64,{_brand_asset('Virtual.otf')}) format('opentype'); font-display: swap; }}
        html, body, .stApp, button, input, textarea, select,
        [data-testid="stMarkdownContainer"], [data-testid="stWidgetLabel"],
        [data-testid="stMetric"], [data-testid="stMetric"] div,
        [data-testid="stCaptionContainer"], [data-testid="stMarkdownContainer"] p,
        [data-testid="stWidgetLabel"] p, [data-testid="stSidebarNav"] a,
        [data-baseweb="radio"] p, [data-baseweb="select"] {{
            font-family: {BRAND_FONT};
        }}
        [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {{
            color: #536176;
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
        [data-testid="stAppViewContainer"] {{ background: #F6F7FB; }}
        .block-container {{ max-width: 1520px; padding-top: 5rem; padding-bottom: 4rem; }}
        h1 {{ font-size: clamp(1.7rem, 3vw, 2.5rem) !important; letter-spacing: -0.035em; }}
        h2, h3 {{ letter-spacing: -0.025em; }}
        [data-testid="stMetric"] {{
            background: white; border: 1px solid #E4E9F2;
            padding: 20px 22px; min-height: 142px;
            box-shadow: 0 3px 12px rgba(0, 14, 61, 0.025);
        }}
        [data-testid="stMetricLabel"] {{ color: #536176; }}
        [data-testid="stMetricValue"] {{ font-family: {BRAND_FONT}; font-size: clamp(1.5rem, 2.5vw, 2.1rem); font-weight: 700; color: #000E3D; }}
        [data-testid="stMetricDelta"] {{ font-size: 0.8rem; }}
        [data-testid="stPlotlyChart"] {{ background: white; border: 1px solid #E4E9F2; border-radius: 12px; padding: 8px; }}
        [data-testid="stSidebar"] {{ border-right: 1px solid #E4E9F2; }}
        [data-testid="stSidebarNav"] a {{ border-radius: 8px; margin: 3px 10px; }}
        [data-testid="stSidebarNav"] a[aria-current="page"] {{ background: #D6E5FF; font-weight: 700; }}
        [data-testid="stExpander"] {{ background: white; border-radius: 12px; }}
        hr {{ border-color: #E4E9F2; margin: 2rem 0; }}

        .isp-header {{
            background: radial-gradient(ellipse at 100% 0%, #123C70 0%, transparent 62%), #08142A;
            border: 1px solid #1B3657; border-radius: 16px; padding: 26px 30px; margin-bottom: 18px;
        }}
        .isp-wordmark {{ display: flex; flex-wrap: wrap; align-items: center; gap: 22px; margin-bottom: 24px; }}
        .isp-wordmark img {{ width: 170px; height: auto; }}
        .isp-wordmark span {{ color: #A3BAC6; font-size: 10px; letter-spacing: 0.14em; }}
        .isp-eyebrow {{ color: #01C8FF; letter-spacing: 0.12em; font-size: 10px; font-weight: 600; }}
        .isp-header h1 {{ color: #FFFFFF; margin: 8px 0 10px; padding: 0; font-size: 2rem !important; }}
        .isp-header p {{ color: #C2D1E0; margin: 0; font-size: 14px; line-height: 1.6; }}
        section[data-testid="stSidebar"] {{ background: #08142A; border-right: 1px solid #1B3657; }}
        [data-testid="stSidebarNav"] a, [data-testid="stSidebarNav"] a span,
        [data-testid="stSidebarNav"] a p {{ color: #C2D1E0; }}
        [data-testid="stSidebarNav"] a:hover {{ background: #122849; }}
        [data-testid="stSidebarNav"] a[aria-current="page"] {{ background: #163963; border-left: 3px solid #01C8FF; }}
        [data-testid="stSidebarNav"] a[aria-current="page"] span {{ color: #FFFFFF; }}
        [data-testid="stSidebarNav"] li > div, [data-testid="stSidebarNav"] h2,
        [data-testid="stSidebarNav"] h3, [data-testid="stNavSectionHeader"] {{ color: #A3BAC6; }}
        [data-testid="stSidebar"] button {{ color: #C2D1E0; }}
        [data-testid="stMetric"] {{ border-top: 3px solid #006FFF; }}
        .stButton > button[kind="primary"] {{ background: #006FFF; color: white; border-color: #006FFF; }}
        .stButton > button:focus-visible, a:focus-visible {{ outline: 2px solid #006FFF; outline-offset: 3px; }}
        .isp-overview-title {{ font-family: 'ISP Virtual', {BRAND_FONT}; color: #08142A; font-size: 2rem; margin: 10px 0 20px; }}

        @media (max-width: 900px) {{
            [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap; }}
            [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {{
                width: 100% !important; flex: 1 1 100% !important; min-width: 0 !important;
            }}
            .block-container {{ padding: 4.5rem 1rem 3rem; }}
            [data-testid="stMetric"] {{ min-height: 115px; padding: 16px; }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


@lru_cache(maxsize=2)
def _brand_asset(name: str) -> str:
    return b64encode((Path(__file__).parent / "assets/maquina-isp" / name).read_bytes()).decode("ascii")


def render_brand_header(title: str, subtitle: str | None = None) -> None:
    """Identidade Máquina ISP; título e contexto da área atual."""
    clean_title = title.lstrip("📣📈🗺️🔻🎯 ")
    st.markdown(
        '<div class="isp-header"><div class="isp-wordmark">'
        f'<img src="data:image/webp;base64,{_brand_asset("logo.webp")}" alt="Máquina ISP" />'
        '<span>INTELIGÊNCIA DE NEGÓCIO</span></div>'
        '<div class="isp-eyebrow">PROJETO INTERNO / MÁQUINA ISP</div>'
        f'<h1>{escape(clean_title)}</h1>'
        f'<p>{escape(subtitle or "")}</p></div>', unsafe_allow_html=True,
    )
