from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Banco -- unica variavel realmente obrigatoria: e a unica de que o dashboard
    # Streamlit precisa.
    database_url: str

    # Credenciais do RD CRM. Opcionais de proposito (default vazio): so a API e os
    # jobs de ingestao usam isso. O dashboard importa `database.connection`, que
    # instancia `Settings()` no import -- se estes campos fossem obrigatorios, o
    # dashboard nao subiria em nenhum host sem receber os segredos do CRM, que ele
    # nunca usa. Quem precisa de verdade valida na hora do uso (ver
    # `require_rd_credentials`).
    rd_crm_client_id: str = ""
    rd_crm_client_secret: str = ""
    rd_crm_redirect_uri: str = ""
    rd_auth_dialog_url: str = "https://accounts.rdstation.com/oauth/authorize"
    rd_token_url: str = "https://api.rd.services/oauth2/token"
    rd_crm_api_base_url: str = "https://api.rd.services/crm/v2"

    # Segredos de autenticacao de endpoints publicos. Vazio = endpoint DESLIGADO,
    # nunca "aceita qualquer coisa" -- ver `_check_token` em api/routes/*.
    rd_webhook_token: str = ""
    sync_trigger_token: str = ""

    # Meta Ads (Marketing API). Opcionais pela mesma razao das credenciais do RD:
    # o dashboard nao pode depender delas pra subir. `meta_access_token` e o token
    # de um USUARIO DO SISTEMA (nao de usuario comum) -- nao expira em 60 dias como
    # um token pessoal, entao nao precisa de fluxo de refresh feito o do RD CRM.
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_access_token: str = ""
    meta_ad_account_id: str = ""
    meta_api_version: str = "v21.0"

    # Google Analytics 4 (Data API). Autenticacao via CONTA DE SERVICO, nao OAuth --
    # ao contrario do Meta, nao ha token de curta duracao pra renovar: uma vez que a
    # conta de servico e adicionada como Visualizador na propriedade GA4, a chave
    # continua valida indefinidamente (ate ser revogada manualmente).
    ga4_property_id: str = ""
    ga4_service_account_json: str = ""

    # Meta Conversions API (CAPI) -- diferente do `meta_access_token` acima (que so
    # LE dados de campanha), isso ENVIA eventos de conversao pro Pixel, exige um
    # token com permissao de escrita gerado especificamente pra isso no Gerenciador
    # de Eventos (Events Manager > Fonte de dados > Configuracoes > API de
    # Conversoes > Gerar token de acesso) -- nao reaproveita o token do Marketing API.
    meta_capi_pixel_id: str = ""
    meta_capi_access_token: str = ""

    # RD CRM: pipeline/etapa que, ao ser alcancada por uma negociacao, dispara o
    # evento de conversao pro Meta (ver webhooks/processor.py). IDs (nao nomes) pra
    # nao quebrar se a etapa for renomeada no RD -- ver `python -m scripts...`
    # pra descobrir o rd_id de uma etapa/pipeline (crm_pipelines/crm_stages).
    meta_capi_trigger_stage_rd_id: str = "6a4febe620cf310024567a82"  # Reuniao Agendada (Qualificacao)
    meta_capi_event_name: str = "Reuniao_Agendada"
    # URL de origem do lead (a landing que o anuncio aponta) -- enviada como
    # `event_source_url` no evento CAPI. Com `action_source="website"`, o Meta usa
    # isso + o `fbc` (do fbclid) pra atribuir a conversao a campanha certa e
    # mostrar no relatorio de anuncios. Nao precisa ser a URL exata que o lead
    # visitou (nao guardamos isso), a raiz da landing ja serve de sinal.
    meta_capi_event_source_url: str = "https://maquina.isp.develcode.com.br/"

    # WhatsApp Cloud API -- canal do agente de atendimento. `whatsapp_access_token`
    # comeca como o token TEMPORARIO do Graph API Explorer (poucas horas de vida,
    # trocar sempre que expirar) ate a revisao do app liberar o token permanente do
    # Usuario do Sistema -- mesma situacao transitoria que vivemos com o Meta Ads.
    whatsapp_phone_number_id: str = ""
    whatsapp_business_account_id: str = ""
    whatsapp_access_token: str = ""
    # Token que NOS escolhemos (string aleatoria propria, nao vem do Meta) e
    # cadastramos na tela de configuracao do Webhook no app -- e o que prova, no
    # handshake inicial (GET), que quem esta configurando o webhook la e quem
    # controla esse servidor aqui.
    whatsapp_verify_token: str = ""
    # Segredo do app (Configuracoes > Basico do app do WhatsApp) -- usado pra
    # validar a assinatura HMAC de cada POST recebido (header X-Hub-Signature-256).
    # Sem isso, qualquer um que descobrisse a URL do webhook poderia mandar
    # "mensagens" falsas que disparariam o agente e acoes no CRM.
    whatsapp_app_secret: str = ""

    # Motor de conversa do agente (Claude). Opcional pela mesma razao das
    # demais credenciais externas -- so quem chama `ingestion/llm/agent.py`
    # precisa disso. `anthropic_model` fica configuravel (nao hardcoded no
    # agente) pra trocar de modelo sem mexer em codigo.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    # Primeiro contato PROATIVO do agente: quando uma negociacao nova cai no
    # CRM com uma dessas origens (rd_id de crm_deal_sources, separados por
    # virgula -- ver `python -m scripts...` ou consultar a tabela pra
    # descobrir o rd_id de uma origem), o agente manda mensagem pro lead
    # sozinho, sem esperar ele escrever primeiro (ver webhooks/processor.py).
    # Default = "Outros | paid_social" (confirmado na base em 2026-09-06).
    whatsapp_agent_trigger_source_rd_ids: str = "6a9a29ffbe2d5f0001fd2555"
    # Nome do TEMPLATE de mensagem aprovado no Meta Business Manager -- e
    # OBRIGATORIO pra iniciar conversa com quem nunca mandou mensagem pro
    # nosso numero (fora da janela de 24h, texto livre e recusado pelo Meta,
    # so template pre-aprovado pode "abrir" a conversa). Vazio = gatilho
    # DESLIGADO (so loga, nao tenta enviar) ate o template existir e ser
    # aprovado -- ver Meta for Developers > WhatsApp > Message Templates.
    whatsapp_agent_template_name: str = ""
    whatsapp_agent_template_language: str = "pt_BR"

    # Trava de piloto controlado pra RESPOSTA A MENSAGEM RECEBIDA (diferente do
    # gatilho de saida acima, que ja e seguro por depender de template). Sem
    # essa trava, o agente responderia QUALQUER mensagem que chegasse no
    # numero, de qualquer lead real. Enquanto vazio (default), o
    # comportamento e esse (responde todo mundo -- so ativar depois de
    # validar bem a qualidade da conversa). Preenchido com numero(s) de
    # telefone (formato da WhatsApp Cloud API -- so digitos com codigo do
    # pais, ex: "5554912345678", separados por virgula), SO responde de
    # verdade quando quem mandou a mensagem e um desses numeros -- pra
    # qualquer outro numero, a mensagem continua sendo recebida e guardada
    # normalmente, so nao gera resposta automatica (mesmo comportamento de
    # antes do agente existir).
    whatsapp_agent_restrict_to_phone_numbers: str = ""

    # Etapa "Interesse Identificado" do pipeline [Máquina ISP] - Qualificação --
    # pra onde o agente move a negociacao quando o lead demonstra interesse em
    # avancar mas AINDA NAO confirmou um horario de reuniao (ver
    # `sinalizar_interesse` em ingestion/llm/agent.py). Confirmado direto na
    # base em 2026-09-06 -- reconfirme se o pipeline for reestruturado.
    rd_stage_interesse_identificado_rd_id: str = "687fe8cbd5677c001aa540b7"

    # Microsoft Graph API (Calendario/Teams) -- usado pelo agente pra CRIAR o
    # evento de verdade na agenda do dono da negociacao quando o lead confirma
    # um horario (ver `confirmar_reuniao`). Autenticacao via APLICATIVO (client
    # credentials, nao OAuth de usuario) -- precisa de um App Registration no
    # Azure AD do tenant com a permissao "Calendars.ReadWrite" do tipo
    # APPLICATION (nao "Delegated") com consentimento de admin, pra poder agir
    # na agenda de qualquer usuario do dominio sem cada um logar. Opcional
    # (vazio = agente so cria a tarefa de "criar a agenda" pra um humano fazer
    # manualmente, sem integrar de verdade -- ver require_microsoft_credentials).
    microsoft_tenant_id: str = ""
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""

    # App
    env: str = "development"
    log_level: str = "INFO"


settings = Settings()


def require_rd_credentials() -> None:
    """Falha cedo e com mensagem clara quando o fluxo OAuth do RD e acionado sem as
    credenciais configuradas. Substitui a validacao que antes vinha "de graca" por
    os campos serem obrigatorios no Settings -- perdemos aquela, mas so pra quem nao
    precisa delas (o dashboard)."""
    faltando = [
        nome
        for nome, valor in (
            ("RD_CRM_CLIENT_ID", settings.rd_crm_client_id),
            ("RD_CRM_CLIENT_SECRET", settings.rd_crm_client_secret),
            ("RD_CRM_REDIRECT_URI", settings.rd_crm_redirect_uri),
        )
        if not valor
    ]
    if faltando:
        raise RuntimeError(
            "Credenciais do RD CRM ausentes: " + ", ".join(faltando)
            + ". Configure no .env (local) ou nas variaveis de ambiente do servico."
        )


def require_meta_credentials() -> None:
    """Mesmo papel de `require_rd_credentials`, para a Marketing API do Meta."""
    faltando = [
        nome
        for nome, valor in (
            ("META_APP_ID", settings.meta_app_id),
            ("META_APP_SECRET", settings.meta_app_secret),
            ("META_ACCESS_TOKEN", settings.meta_access_token),
            ("META_AD_ACCOUNT_ID", settings.meta_ad_account_id),
        )
        if not valor
    ]
    if faltando:
        raise RuntimeError(
            "Credenciais do Meta Ads ausentes: " + ", ".join(faltando)
            + ". Configure no .env (local) ou nas variaveis de ambiente do servico."
        )


def require_ga4_credentials() -> None:
    """Mesmo papel de `require_rd_credentials`, para a GA4 Data API."""
    faltando = [
        nome
        for nome, valor in (
            ("GA4_PROPERTY_ID", settings.ga4_property_id),
            ("GA4_SERVICE_ACCOUNT_JSON", settings.ga4_service_account_json),
        )
        if not valor
    ]
    if faltando:
        raise RuntimeError(
            "Credenciais do GA4 ausentes: " + ", ".join(faltando)
            + ". Configure no .env (local) ou nas variaveis de ambiente do servico."
        )


def require_meta_capi_credentials() -> None:
    """Mesmo papel de `require_rd_credentials`, para a Meta Conversions API."""
    faltando = [
        nome
        for nome, valor in (
            ("META_CAPI_PIXEL_ID", settings.meta_capi_pixel_id),
            ("META_CAPI_ACCESS_TOKEN", settings.meta_capi_access_token),
        )
        if not valor
    ]
    if faltando:
        raise RuntimeError(
            "Credenciais da Meta Conversions API ausentes: " + ", ".join(faltando)
            + ". Configure no .env (local) ou nas variaveis de ambiente do servico."
        )


def require_whatsapp_credentials() -> None:
    """Mesmo papel de `require_rd_credentials`, para o WhatsApp Cloud API."""
    faltando = [
        nome
        for nome, valor in (
            ("WHATSAPP_PHONE_NUMBER_ID", settings.whatsapp_phone_number_id),
            ("WHATSAPP_ACCESS_TOKEN", settings.whatsapp_access_token),
        )
        if not valor
    ]
    if faltando:
        raise RuntimeError(
            "Credenciais do WhatsApp ausentes: " + ", ".join(faltando)
            + ". Configure no .env (local) ou nas variaveis de ambiente do servico."
        )


def require_anthropic_credentials() -> None:
    """Mesmo papel de `require_rd_credentials`, para o motor de conversa (Claude)."""
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY ausente. Configure no .env (local) ou nas variaveis de "
            "ambiente do servico -- crie a chave em https://console.anthropic.com/settings/keys."
        )


def require_microsoft_credentials() -> None:
    """Mesmo papel de `require_rd_credentials`, para o Microsoft Graph API
    (agenda/Teams)."""
    faltando = [
        nome
        for nome, valor in (
            ("MICROSOFT_TENANT_ID", settings.microsoft_tenant_id),
            ("MICROSOFT_CLIENT_ID", settings.microsoft_client_id),
            ("MICROSOFT_CLIENT_SECRET", settings.microsoft_client_secret),
        )
        if not valor
    ]
    if faltando:
        raise RuntimeError(
            "Credenciais do Microsoft Graph ausentes: " + ", ".join(faltando)
            + ". Configure no .env (local) ou nas variaveis de ambiente do servico -- "
            "ver docstring de config/settings.py pra como criar o App Registration no Azure AD."
        )
