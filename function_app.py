"""Azure Functions (Timer Trigger) -- substitui os crons que rodavam no GitHub
Actions (.github/workflows/sync-incremental.yml e lembretes-reuniao.yml).

Motivo da migracao: repositorio privado no GitHub Free tem so 2.000 minutos
gratis de Actions/mes -- dois crons de 15 em 15 min estouraram isso em pouco
mais de 2 semanas (ver commit que remove os workflows), e sem limite de gasto
configurado o GitHub simplesmente PAROU de criar novos runs agendados, em
silencio (sem erro, sem run vermelho -- so parou de aparecer). Azure
Functions no plano de Consumo tem 1 milhao de execucoes gratis/mes -- pra
2 jobs de 15 em 15 min (~5760 execucoes/mes juntos) isso deve ficar em R$0.

O Render (FastAPI + Streamlit) continua exatamente como esta -- so os jobs
agendados saem de la. Deploy: `host.json` + este arquivo ficam na raiz do
repositorio de proposito (nao numa subpasta separada), pra reusar direto os
mesmos modulos (`ingestion`, `scripts`, `database`, `config`) sem duplicar
nada -- ver `.funcignore` pra saber o que NAO vai no pacote de deploy.

Configuracao necessaria no Function App (Azure Portal > Configuration >
Application settings -- mesmas credenciais ja usadas no Render/GitHub
Actions): DATABASE_URL, RD_CRM_CLIENT_ID, RD_CRM_CLIENT_SECRET,
RD_CRM_REDIRECT_URI, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_ACCESS_TOKEN. As
demais (META_*, GA4_*) sao opcionais -- ausentes, o sync so pula essa etapa
(ver ingestion/sync_all.py).

PENDENCIA CONHECIDA (2026-09-14): a Azure Function PRECISA de "azure-functions"
no requirements.txt do mesmo diretorio do host.json (nao e fornecido pelo
runtime -- confirmado na documentacao oficial). Como este arquivo fica na raiz
do repositorio, isso colidiria com o requirements.txt que o Streamlit Community
Cloud le (e ja quebrou o deploy do dashboard uma vez, por isso "azure-functions"
NAO esta mais no requirements.txt da raiz). Ainda falta decidir/testar a forma
certa de dar esse pacote pro Azure sem voltar a quebrar o Streamlit -- possiveis
caminhos: build customizado no Deployment Center que instala um requirements
extra, ou mover host.json pra uma subpasta com deploy do repositorio inteiro
mesmo assim. So resolver testando contra o Function App de verdade, depois que
ele existir no Azure -- ver conversa de 2026-09-14."""

import logging

import azure.functions as func

app = func.FunctionApp()


@app.timer_trigger(schedule="0 */15 * * * *", arg_name="timer", run_on_startup=False, use_monitor=True)
def sync_e_reprocessar_leads(timer: func.TimerRequest) -> None:
    """Sincronizacao incremental do RD CRM + reprocessamento de lead de
    trafego pago sem primeiro contato -- nessa ordem, na MESMA execucao (nao
    em functions/schedules separados), igual rodava no GitHub Actions: o
    reprocessamento precisa que o contato ja esteja sincronizado, senao cai
    na mesma corrida que ele existe pra fechar (ver docstring de
    scripts/reprocessar_leads_trafego_pago.py)."""
    from ingestion.sync_all import run_incremental_sync
    from scripts.reprocessar_leads_trafego_pago import main as reprocessar_leads

    logging.info("Azure Function: iniciando sincronizacao incremental do RD CRM.")
    faltando = run_incremental_sync()
    if faltando:
        logging.warning("Azure Function: sync incremental com pendencias em: %s", ", ".join(sorted(faltando)))
    else:
        logging.info("Azure Function: sync incremental concluido sem pendencias.")

    logging.info("Azure Function: iniciando reprocessamento de leads de trafego pago.")
    reprocessar_leads()
    logging.info("Azure Function: reprocessamento concluido.")


@app.timer_trigger(schedule="0 */15 * * * *", arg_name="timer", run_on_startup=False, use_monitor=True)
def lembretes_reuniao(timer: func.TimerRequest) -> None:
    """Lembretes automaticos de reuniao (vespera 20h, manha 08h, 1h antes) +
    varredura de no-show -- mesma logica que rodava em
    .github/workflows/lembretes-reuniao.yml."""
    from scripts.enviar_lembretes_reuniao import main as enviar_lembretes

    logging.info("Azure Function: iniciando envio de lembretes de reuniao.")
    enviar_lembretes()
    logging.info("Azure Function: lembretes concluidos.")
