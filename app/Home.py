import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # garante que "app"/"database" sejam importaveis

import streamlit as st

from app.theme import inject_brand, render_brand_header

st.set_page_config(page_title="Marketing & Sales Data Hub", page_icon="📊", layout="wide")
inject_brand()

render_brand_header("Marketing & Sales Data Hub", "Dados do RD Station CRM + Melhor Venda, sincronizados automaticamente.")

st.markdown(
    """
Use o menu à esquerda para navegar:

- **🎯 Melhor Venda** — volume de leads, taxa de conexão e o que virou negociação no CRM
- **🔻 Funil Máquina ISP** — funil canônico, aging por etapa, performance de SDR e Closer

Mais páginas (ThunderIA e demais produtos) chegam conforme o funil de cada um for mapeado.
"""
)
