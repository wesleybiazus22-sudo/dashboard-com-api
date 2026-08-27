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
