import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import streamlit as st
from app.theme import inject_brand, render_brand_header

st.set_page_config(page_title="Máquina ISP | Visão geral", page_icon="📊", layout="wide")
inject_brand()
render_brand_header("Máquina ISP", "Projeto interno · Aquisição, conversão e desempenho comercial.")
st.markdown('<div class="isp-overview-title">Do primeiro contato ao resultado.</div>', unsafe_allow_html=True)
st.caption("Todas as análises abaixo pertencem ao Máquina ISP. Escolha uma área para explorar seus indicadores.")
pages = [
    ("Funil Máquina ISP", "Onde as negociações avançam e quais etapas precisam de atenção.", "2_"),
    ("Melhor Venda", "Volume de prospecção, conexão e desempenho por campanha e SDR.", "1_"),
    ("Meta Ads", "Investimento, leads e eficiência das campanhas e criativos.", "4_"),
    ("Google Analytics", "Tráfego, engajamento e canais que trazem visitantes.", "5_"),
    ("Geografia", "Distribuição da carteira e conversão por região e cidade.", "3_"),
    ("Agente de atendimento", "Custo de IA e mensageria, e histórico de conversas do WhatsApp.", "6_"),
]
for start in range(0, len(pages), 2):
    for col, (title, description, prefix) in zip(st.columns(2), pages[start:start+2]):
        with col, st.container(border=True):
            st.subheader(title)
            st.write(description)
            page = next((Path(__file__).parent / "pages").glob(prefix + "*.py"))
            routes = {"2_": "Funil_Maquina_ISP", "1_": "Melhor_Venda", "4_": "Meta_Ads", "5_": "GA4", "3_": "Geografia", "6_": "Agente"}
            st.page_link(st.Page(str(page), title=title, url_path=routes[prefix]), label="Explorar →", use_container_width=True)
st.caption("Comece pelo funil para avaliar conversão. Use os canais para investigar aquisição e eficiência.")
