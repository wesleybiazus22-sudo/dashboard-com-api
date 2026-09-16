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

RESOLVIDO (2026-09-14/16): duas pendencias encadeadas pra fazer o deploy da
Azure Function (plano Flex Consumption) funcionar --

1. Auth: o workflow que o Azure Portal criou sozinho ao conectar o Deployment
   Center usava slot-name (nao suportado em Flex Consumption) e dava 401 no
   Kudu. Corrigido com sku: flexconsumption + remote-build: true (correcao do
   TI em 2026-09-15).

2. Timeout do build remoto: com remote-build: true, o Azure roda "pip install"
   remotamente a partir do requirements.txt que veio no pacote -- e o
   requirements.txt da raiz (que o Streamlit Community Cloud tambem le, entao
   nao pode ganhar azure-functions nem perder as libs do dashboard) tem
   fastapi/uvicorn/streamlit/plotly/pandas que a Function nem usa. O remote
   build do Flex Consumption tem timeout de 60s pra instalar tudo -- isso
   estourava o limite (erro "OneDeploy"/"ZIP Deploy failed"). Resolvido com
   requirements-azure.txt (so as libs que ingestion/, scripts/, database/ e
   config/ realmente usam, mais azure-functions) -- o workflow troca o
   requirements.txt pela versao enxuta SO na copia zipada, no runner do CI,
   sem tocar no arquivo versionado."""

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
