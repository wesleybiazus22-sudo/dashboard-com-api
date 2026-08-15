from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Banco
    database_url: str

    # RD CRM OAuth
    rd_crm_client_id: str
    rd_crm_client_secret: str
    rd_crm_redirect_uri: str
    rd_auth_dialog_url: str = "https://api.rd.services/auth/dialog"
    rd_token_url: str = "https://api.rd.services/auth/token"
    rd_crm_api_base_url: str = "https://api.rd.services/crm/v2"

    # Webhook
    rd_webhook_token: str

    # App
    env: str = "development"
    log_level: str = "INFO"


settings = Settings()
