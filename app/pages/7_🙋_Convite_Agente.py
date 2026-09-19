"""SDR dispara manualmente o convite do agente pra UM contato específico de uma
negociação de prospecção ativa ("Melhor Venda") -- ver whatsapp/manual_invite.py
pra regras/idempotência. Página de AÇÃO (grava no banco e manda WhatsApp de
verdade), diferente das demais páginas do dashboard, que só leem."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # garante que "app"/"database" sejam importaveis

import streamlit as st

from app.theme import inject_brand, render_brand_header
from config.settings import settings
from database.connection import session_scope
from database.models import CrmDeal
from ingestion.whatsapp.client import normalizar_telefone_br
from whatsapp.manual_invite import contatos_do_card, enviar_convite_manual, ja_convidados

st.set_page_config(page_title="Convite Agente", page_icon="🙋", layout="wide")
inject_brand()

render_brand_header(
    "🙋 Convite manual pro Agente",
    "Prospecção ativa (Melhor Venda): ofereça a UM contato específico uma demonstração da conversa com o agente.",
)

if not settings.whatsapp_agent_melhor_venda_template_name.strip():
    st.warning(
        "O template do convite manual ainda não foi configurado (aguardando aprovação no Meta Business Manager). "
        "A busca funciona normalmente, mas o botão de envio ficará bloqueado até o template ser aprovado.",
        icon="⏳",
    )

st.divider()

busca = st.text_input(
    "Buscar negociação de Melhor Venda (nome da empresa ou do contato)",
    placeholder="Ex: Click Internet, ou João Silva",
)
termo = busca.strip().lower()

origens_permitidas = {
    s.strip() for s in settings.whatsapp_agent_melhor_venda_source_rd_ids.split(",") if s.strip()
}

with session_scope() as db:
    base = (
        db.query(CrmDeal)
        .filter(CrmDeal.source.in_(origens_permitidas), CrmDeal.status == "ongoing")
        .order_by(CrmDeal.deal_created_at.desc())
    )
    candidatos = base.all() if termo else base.limit(20).all()

    resultados = []
    for deal in candidatos:
        contatos = contatos_do_card(db, deal)
        bate = (
            not termo
            or termo in (deal.name or "").lower()
            or any(termo in (c.name or "").lower() for c in contatos)
        )
        if bate:
            resultados.append((deal, contatos))

    if not termo:
        st.caption("Mostrando as 20 negociações de Melhor Venda em aberto mais recentes. Use a busca pra achar outra.")

    if not resultados:
        st.info("Nenhuma negociação encontrada." if termo else "Nenhuma negociação de Melhor Venda em aberto no momento.")

    for deal, contatos in resultados:
        convidados = ja_convidados(db, deal.rd_id)

        with st.expander(f"{deal.name}  ·  {len(contatos)} contato(s)", expanded=bool(termo)):
            if not contatos:
                st.caption("Nenhum contato sincronizado pra esse card ainda.")
                continue

            for contato in contatos:
                telefone_normalizado = normalizar_telefone_br(contato.phone) if contato.phone else None
                ja_recebeu = bool(telefone_normalizado) and telefone_normalizado in convidados

                col_nome, col_tel, col_status, col_acao = st.columns([2, 1.4, 1.6, 1.6])
                col_nome.write(f"**{contato.name or '(sem nome)'}**")
                col_tel.write(contato.phone or "—")

                if ja_recebeu:
                    col_status.success("Convidado")
                elif not telefone_normalizado:
                    col_status.caption("Sem telefone válido")
                else:
                    col_status.caption("Ainda não convidado")

                chave = f"{deal.rd_id}::{contato.rd_id}"
                pode_enviar = not ja_recebeu and telefone_normalizado

                with col_acao:
                    if st.button("Enviar convite", key=f"btn::{chave}", disabled=not pode_enviar, use_container_width=True):
                        st.session_state[f"confirmar::{chave}"] = True

                if pode_enviar and st.session_state.get(f"confirmar::{chave}"):
                    with st.form(key=f"form::{chave}"):
                        sdr_nome = st.text_input(
                            "Seu nome (SDR) -- aparece na mensagem pro lead",
                            value=st.session_state.get("ultima_sdr_nome", ""),
                            key=f"sdr::{chave}",
                        )
                        confirmar, cancelar = st.columns(2)
                        enviar = confirmar.form_submit_button(f"Confirmar envio pra {contato.name}", type="primary")
                        desistir = cancelar.form_submit_button("Cancelar")

                        if desistir:
                            st.session_state.pop(f"confirmar::{chave}", None)
                            st.rerun()

                        if enviar:
                            if not sdr_nome.strip():
                                st.error("Informe seu nome antes de enviar.")
                            else:
                                st.session_state["ultima_sdr_nome"] = sdr_nome.strip()
                                sucesso, mensagem = enviar_convite_manual(
                                    db, deal=deal, contact=contato, sdr_nome=sdr_nome.strip(),
                                )
                                if sucesso:
                                    st.session_state.pop(f"confirmar::{chave}", None)
                                    st.success(mensagem)
                                    st.rerun()
                                else:
                                    st.error(mensagem)
