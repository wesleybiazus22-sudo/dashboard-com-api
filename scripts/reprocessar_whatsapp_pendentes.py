"""Pega o lead de trafego pago que mandou a primeira mensagem no WhatsApp
ANTES do card sincronizar no CRM -- e por isso ficou sem resposta -- e aciona
o agente agora que o card ja existe.

Contexto (ver whatsapp/processor.py::_lead_de_trafego_pago): o agente so
responde se achar a negociacao do telefone no CRM e ela for de trafego pago.
Mas o fluxo real e: lead preenche o formulario -> e jogado pro WhatsApp com
mensagem pre-preenchida -> RD Marketing manda pro CRM. A mensagem pode chegar
alguns minutos ANTES do card. Na primeira tentativa o agente pula (sem card);
este script fecha essa janela.

Regra pra acionar (deliberadamente conservadora -- so pega a corrida de
"primeiro contato", nunca mexe em conversa ja em andamento):
  - existe mensagem RECEBIDA nos ultimos JANELA_MIN minutos, E
  - NUNCA houve mensagem ENVIADA pra esse telefone (agente nunca engajou), E
  - agora `_deal_por_telefone` acha uma negociacao de trafego pago.

Uso: python -m scripts.reprocessar_whatsapp_pendentes
Ideal rodar a cada ~5 min (cron do Render, ou GitHub Actions).
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from database.connection import session_scope
from database.models import WhatsappMessage
from whatsapp.processor import (
    _deal_por_telefone,
    _lead_de_trafego_pago,
    _pode_iniciar_atendimento,
    _responder_com_agente,
)

JANELA_MIN = 30


def main() -> None:
    corte = datetime.now(timezone.utc) - timedelta(minutes=JANELA_MIN)
    acionados = 0

    with session_scope() as db:
        # telefones com mensagem recebida recente
        telefones = [
            row[0]
            for row in db.query(WhatsappMessage.phone_number)
            .filter(WhatsappMessage.direction == "inbound", WhatsappMessage.occurred_at >= corte)
            .distinct()
            .all()
        ]

        for telefone in telefones:
            # o agente ja respondeu esse telefone alguma vez? entao nao e corrida
            # de primeiro contato -- o fluxo normal do webhook cuida das proximas.
            ja_respondido = (
                db.query(func.count(WhatsappMessage.id))
                .filter(WhatsappMessage.phone_number == telefone, WhatsappMessage.direction == "outbound")
                .scalar()
            )
            if ja_respondido:
                continue

            deal = _deal_por_telefone(db, telefone)
            if not _lead_de_trafego_pago(db, deal):
                continue  # card ainda nao apareceu, ou nao e trafego pago -- proxima rodada tenta de novo
            if not _pode_iniciar_atendimento(db, telefone, deal):
                continue  # ja passou de "Primeira Conexao" -- humano assumiu, agente nao inicia

            ultima = (
                db.query(WhatsappMessage)
                .filter(WhatsappMessage.phone_number == telefone, WhatsappMessage.direction == "inbound")
                .order_by(WhatsappMessage.occurred_at.desc())
                .first()
            )
            if not ultima or not ultima.text_body:
                continue

            print(f"Acionando agente pro telefone {telefone} (negociacao {deal.rd_id}, atrasado pela sincronia do card)")
            _responder_com_agente(
                db,
                phone_number=telefone,
                texto=ultima.text_body,
                wamid_recebido=ultima.wamid,
                contact_name=ultima.contact_name,
            )
            acionados += 1

    print(f"{acionados} lead(s) de trafego pago acionados nesta rodada.")


if __name__ == "__main__":
    main()
