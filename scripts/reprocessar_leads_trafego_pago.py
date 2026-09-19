"""Pega negociacao de trafego pago que ainda NAO recebeu o template de
abertura do agente e tenta de novo -- fecha a corrida em que o webhook da
negociacao chega no Render ANTES do contato sincronizar pro nosso banco (a
negociacao e o contato costumam nascer quase juntos numa entrada de lead
real; nosso sync roda a cada 15 min). Nesse caso,
`_iniciar_atendimento_agente` nao acha telefone e pula o envio SEM tentar de
novo sozinha (ver webhooks/processor.py) -- a menos que outro webhook chegue
pra essa negociacao depois, o que pode nunca acontecer.

IMPORTANTE: precisa rodar DEPOIS do sync incremental, no MESMO job do GitHub
Actions (ver .github/workflows/sync-incremental.yml) -- assim o contato
sempre ja esta sincronizado quando este script roda, fechando a corrida por
CONSTRUCAO (ordem garantida dentro do job), nao por sorte de timing entre
dois crons separados.

Mesmo padrao de `scripts/reprocessar_whatsapp_pendentes.py` (que cobre a
corrida oposta: mensagem do WhatsApp chegando antes do card sincronizar).

Janela de 24h: negociacao mais velha que isso sem template mandado
provavelmente tem outro motivo (ex: sem telefone valido de verdade, nao so
atraso de sync) -- reprocessar pra sempre so geraria tentativas inuteis, e
reabrir contato com um lead de dias atras fora de contexto nao e desejavel.

Uso: python -m scripts.reprocessar_leads_trafego_pago
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_

from config.settings import settings
from database.connection import session_scope
from database.models import CrmDeal, WhatsappMessage
from webhooks.processor import _iniciar_atendimento_agente

JANELA_HORAS = 24


def main() -> None:
    # Mesmo OU-logico de `_iniciar_atendimento_agente` (source OU utm_medium --
    # ver webhooks/processor.py e docstring la pra motivo de ter os dois).
    origens_gatilho = {s.strip() for s in settings.whatsapp_agent_trigger_source_rd_ids.split(",") if s.strip()}
    utm_mediums_gatilho = {
        s.strip().lower() for s in settings.whatsapp_agent_trigger_utm_mediums.split(",") if s.strip()
    }
    condicoes_origem = []
    if origens_gatilho:
        condicoes_origem.append(CrmDeal.source.in_(origens_gatilho))
    if utm_mediums_gatilho:
        condicoes_origem.append(func.lower(CrmDeal.utm_medium).in_(utm_mediums_gatilho))
    if not condicoes_origem or not settings.whatsapp_agent_template_name:
        print("Gatilho de abertura desligado (origem/utm_medium ou template nao configurado) -- nada a reprocessar.")
        return

    corte = datetime.now(timezone.utc) - timedelta(hours=JANELA_HORAS)
    tentados = 0

    with session_scope() as db:
        candidatos = (
            db.query(CrmDeal)
            .filter(
                or_(*condicoes_origem),
                CrmDeal.status == "ongoing",
                CrmDeal.deal_created_at >= corte,
                ~CrmDeal.rd_id.in_(
                    db.query(WhatsappMessage.deal_rd_id).filter(
                        WhatsappMessage.message_type == "template", WhatsappMessage.deal_rd_id.isnot(None),
                    )
                ),
            )
            .all()
        )

        for deal in candidatos:
            print(f"Reprocessando negociação {deal.rd_id} ({deal.name}) -- tentando primeiro contato de novo.")
            _iniciar_atendimento_agente(db, deal)
            tentados += 1

    print(f"{tentados} negociação(ões) de tráfego pago reprocessada(s).")


if __name__ == "__main__":
    main()
