"""Entrada única do projeto Máquina ISP."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import streamlit as st

root = Path(__file__).resolve().parent
def section(prefix, title, path, icon):
    file = next((root / "pages").glob(prefix + "*.py"))
    return st.Page(str(file), title=title, url_path=path, icon=icon)

page = st.navigation({"Máquina ISP": [
    st.Page(str(root / "overview.py"), title="Visão geral", icon="🏠", default=True),
    section("2_", "Funil comercial", "Funil_Maquina_ISP", "📊"),
    section("1_", "Prospecção · Melhor Venda", "Melhor_Venda", "🎯"),
    section("4_", "Mídia paga · Meta Ads", "Meta_Ads", "📣"),
    section("5_", "Tráfego · Google Analytics", "GA4", "📈"),
    section("3_", "Distribuição geográfica", "Geografia", "🗺️"),
]}, expanded=True)
page.run()
