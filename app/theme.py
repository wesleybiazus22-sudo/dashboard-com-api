"""Paleta compartilhada (validada para daltonismo) usada em todos os graficos do
dashboard. Ordem categorica fixa -- nunca ciclar, nunca reatribuir por rank."""

# Categorica (identidade), ordem fixa
CAT_BLUE = "#2a78d6"
CAT_ORANGE = "#eb6834"
CAT_AQUA = "#1baf7a"
CAT_YELLOW = "#eda100"
CAT_MAGENTA = "#e87ba4"
CAT_GREEN = "#008300"
CAT_VIOLET = "#4a3aa7"
CAT_RED = "#e34948"

CATEGORICAL = [CAT_BLUE, CAT_ORANGE, CAT_AQUA, CAT_YELLOW, CAT_MAGENTA, CAT_GREEN, CAT_VIOLET, CAT_RED]

# Sequencial azul (magnitude/ordinal) -- para funil de etapas ordenadas
SEQUENTIAL_BLUE = ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab"]

# Status (fixo, nunca reusado como serie)
STATUS_GOOD = "#0ca30c"
STATUS_WARNING = "#fab219"
STATUS_SERIOUS = "#ec835a"
STATUS_CRITICAL = "#d03b3b"
STATUS_NEUTRAL = "#898781"  # muted ink -- para estados "sem retorno" (nem bom nem ruim)

# Chrome / ink
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
TEXT_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
CHART_SURFACE = "#fcfcfb"

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


def base_layout(fig, height: int = 420):
    """Aplica chrome consistente (grid recessivo, fundo, fonte) a uma figura Plotly."""
    fig.update_layout(
        height=height,
        plot_bgcolor=CHART_SURFACE,
        paper_bgcolor=CHART_SURFACE,
        font=dict(color=TEXT_PRIMARY, family="system-ui, -apple-system, 'Segoe UI', sans-serif"),
        margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    fig.update_xaxes(gridcolor=GRIDLINE, zerolinecolor=GRIDLINE)
    fig.update_yaxes(gridcolor=GRIDLINE, zerolinecolor=GRIDLINE)
    return fig
