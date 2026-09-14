"""Dispara os 3 lembretes automaticos de reuniao (vespera 20h do dia anterior,
manha 08h do dia, 1h antes do horario marcado) e puxa pra "No-show" quem
passou da reuniao sem confirmar.

Escopo: QUALQUER negociacao do pipeline [Máquina ISP] - Qualificação que
esteja na etapa "Reunião Agendada" e tenha uma tarefa `crm_tasks` tipo
'meeting' aberta com `due_at` preenchido -- trafego pago ou nao. Decisao do
dono do produto em 2026-09-11: `crm_tasks.due_at`, que a SDR ja preenche
manualmente ha meses ao marcar reuniao no RD (fonte confirmada contra a base
real, independente da nossa automacao), e a fonte de verdade -- nao precisa
consultar o Microsoft Graph pra saber QUANDO e a reuniao. O agente so responde
essas conversas porque `whatsapp/processor.py::_responder_com_agente` libera
quem ja recebeu alguma mensagem do agente antes (ver `_agente_ja_engajou`) --
o primeiro lembrete enviado aqui e o que abre essa porta.

Idempotente via `agente_lembretes_reuniao` (1 linha por negociacao+horario):
cada lembrete so e mandado uma vez por horario de reuniao. Reagendamento
(devido `crm_tasks.due_at` mudar, via `reagendar_reuniao` no agente ou edicao
manual no RD) cria uma linha nova automaticamente -- os lembretes recomecam
do zero pro novo horario.

No-show: se a tarefa continuar 'open' no RD (ninguem marcou como concluida)
45 min depois do horario da reuniao, a negociacao vai pra "No-show" --
sinal de que a reuniao nao aconteceu e ninguem tratou isso ainda.

Uso: python -m scripts.enviar_lembretes_reuniao
Ideal rodar a cada 15 min (GitHub Actions -- ver
.github/workflows/lembretes-reuniao.yml). Tolerancia de +-10min em cada
janela cobre folgas do agendador (mesmo criterio do sync incremental)."""

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config.settings import settings
from database.connection import session_scope
from database.models import AgenteLembreteReuniao, CrmContact, CrmDeal, CrmTask, WhatsappMessage
from ingestion.rd_crm.actions import mover_negociacao_para_etapa
from ingestion.whatsapp.client import WhatsappClient, normalizar_telefone_br

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_FUSO = ZoneInfo("America/Sao_Paulo")
_TOLERANCIA = timedelta(minutes=10)
_ATRASO_NO_SHOW = timedelta(minutes=45)

_TEMPLATES_POR_TIPO = {
    "vespera": lambda: settings.whatsapp_agent_reminder_template_vespera,
    "manha": lambda: settings.whatsapp_agent_reminder_template_manha,
    "1h_antes": lambda: settings.whatsapp_agent_reminder_template_1h_antes,
}


def _primeiro_nome(nome_completo: str | None) -> str:
    if nome_completo and nome_completo.strip():
        return nome_completo.strip().split()[0].capitalize()
    return "tudo bem"


def _enviar_lembrete(db, *, deal: CrmDeal, telefone: str, nome: str, data_fmt: str, hora_fmt: str, tipo: str) -> bool:
    """Manda o template do tipo pedido. Devolve True se enviou (pra so entao
    marcar o controle como feito) -- False se nao tem template configurado ou
    se a chamada falhou (proxima rodada tenta de novo, sem marcar nada)."""
    template_name = _TEMPLATES_POR_TIPO[tipo]()
    if not template_name:
        return False
    try:
        resposta = WhatsappClient().send_template(
            to=telefone,
            template_name=template_name,
            language_code=settings.whatsapp_agent_reminder_template_language,
            components=[{
                "type": "body",
                "parameters": [
                    {"type": "text", "text": nome},
                    {"type": "text", "text": data_fmt},
                    {"type": "text", "text": hora_fmt},
                ],
            }],
        )
        wamid = (resposta.get("messages") or [{}])[0].get("id")
        db.add(WhatsappMessage(
            wamid=wamid or f"outbound-sem-id:{deal.rd_id}:{tipo}:{datetime.now(timezone.utc).timestamp()}",
            phone_number=telefone,
            direction="outbound",
            message_type="template",
            text_body=f"[lembrete de reunião -- {tipo}: {template_name}]",
            deal_rd_id=deal.rd_id,
            raw=resposta,
            occurred_at=datetime.now(timezone.utc),
        ))
        logger.info("Lembrete '%s' enviado pra negociação %s (telefone %s).", tipo, deal.rd_id, telefone)
        return True
    except Exception:  # noqa: BLE001 -- proxima rodada tenta de novo, nunca derruba o job inteiro
        logger.exception("Falha ao mandar lembrete '%s' pra negociação %s.", tipo, deal.rd_id)
        return False


def _processar_lembretes(db, agora: datetime) -> int:
    tarefas = (
        db.query(CrmTask, CrmDeal, CrmContact)
        .join(CrmDeal, CrmDeal.rd_id == CrmTask.deal_rd_id)
        .outerjoin(CrmContact, CrmContact.rd_id == CrmDeal.contact_rd_id)
        .filter(
            CrmTask.type == "meeting",
            CrmTask.status == "open",
            CrmTask.due_at.isnot(None),
            CrmDeal.pipeline_rd_id == settings.rd_pipeline_maquina_isp_qualificacao_rd_id,
            CrmDeal.stage_rd_id == settings.meta_capi_trigger_stage_rd_id,
        )
        .all()
    )

    enviados = 0
    for tarefa, deal, contato in tarefas:
        telefone = normalizar_telefone_br(contato.phone if contato else None)
        if not telefone:
            continue

        due_at_local = tarefa.due_at.astimezone(_FUSO)
        vespera_alvo = (due_at_local - timedelta(days=1)).replace(hour=20, minute=0, second=0, microsecond=0)
        manha_alvo = due_at_local.replace(hour=8, minute=0, second=0, microsecond=0)
        uma_hora_antes_alvo = due_at_local - timedelta(hours=1)

        controle = (
            db.query(AgenteLembreteReuniao)
            .filter(AgenteLembreteReuniao.deal_rd_id == deal.rd_id, AgenteLembreteReuniao.reuniao_due_at == tarefa.due_at)
            .one_or_none()
        )
        if controle is None:
            controle = AgenteLembreteReuniao(deal_rd_id=deal.rd_id, reuniao_due_at=tarefa.due_at)
            db.add(controle)
            db.flush()

        nome = _primeiro_nome(contato.name if contato else None)
        data_fmt = due_at_local.strftime("%d/%m")
        hora_fmt = due_at_local.strftime("%H:%M")

        # So um lembrete por rodada por negociacao, na ordem cronologica -- evita
        # mandar 2 de uma vez se o job ficou parado e "pulou" uma janela.
        if (
            controle.vespera_enviado_em is None
            and agora >= vespera_alvo - _TOLERANCIA
            and agora < manha_alvo
        ):
            if _enviar_lembrete(db, deal=deal, telefone=telefone, nome=nome, data_fmt=data_fmt, hora_fmt=hora_fmt, tipo="vespera"):
                controle.vespera_enviado_em = agora
                enviados += 1

        elif (
            controle.manha_enviado_em is None
            and agora >= manha_alvo - _TOLERANCIA
            and agora < uma_hora_antes_alvo
        ):
            if _enviar_lembrete(db, deal=deal, telefone=telefone, nome=nome, data_fmt=data_fmt, hora_fmt=hora_fmt, tipo="manha"):
                controle.manha_enviado_em = agora
                enviados += 1

        elif (
            controle.uma_hora_antes_enviado_em is None
            and agora >= uma_hora_antes_alvo - _TOLERANCIA
            and agora < due_at_local
        ):
            if _enviar_lembrete(db, deal=deal, telefone=telefone, nome=nome, data_fmt=data_fmt, hora_fmt=hora_fmt, tipo="1h_antes"):
                controle.uma_hora_antes_enviado_em = agora
                enviados += 1

        db.commit()

    return enviados


def _marcar_no_show(db, agora: datetime) -> int:
    limite = agora - _ATRASO_NO_SHOW
    tarefas = (
        db.query(CrmTask, CrmDeal)
        .join(CrmDeal, CrmDeal.rd_id == CrmTask.deal_rd_id)
        .filter(
            CrmTask.type == "meeting",
            CrmTask.status == "open",
            CrmTask.due_at.isnot(None),
            CrmTask.due_at <= limite,
            CrmDeal.pipeline_rd_id == settings.rd_pipeline_maquina_isp_qualificacao_rd_id,
            CrmDeal.stage_rd_id == settings.meta_capi_trigger_stage_rd_id,
        )
        .all()
    )

    marcados = 0
    for tarefa, deal in tarefas:
        try:
            mover_negociacao_para_etapa(db, deal.rd_id, settings.rd_stage_no_show_rd_id)
            controle = (
                db.query(AgenteLembreteReuniao)
                .filter(AgenteLembreteReuniao.deal_rd_id == deal.rd_id, AgenteLembreteReuniao.reuniao_due_at == tarefa.due_at)
                .one_or_none()
            )
            if controle:
                controle.no_show_marcado_em = agora
            db.commit()
            logger.info("Negociação %s puxada pra 'No-show' (reunião %s sem confirmação).", deal.rd_id, tarefa.due_at)
            marcados += 1
        except Exception:  # noqa: BLE001 -- proxima rodada tenta de novo
            db.rollback()
            logger.exception("Falha ao puxar negociação %s pra 'No-show'.", deal.rd_id)

    return marcados


def main() -> None:
    agora = datetime.now(_FUSO)
    with session_scope() as db:
        enviados = _processar_lembretes(db, agora)
        marcados = _marcar_no_show(db, agora)
    print(f"{enviados} lembrete(s) enviado(s), {marcados} negociação(ões) puxada(s) pra No-show.")


if __name__ == "__main__":
    main()
