"""
Gerencia o ciclo de vida do OAuth do RD Station CRM:
- troca do "code" (recebido no /auth/rd/callback) por access_token + refresh_token
- refresh automático quando o token expira
- persistência centralizada em rd_oauth_tokens (uma linha por produto, sempre a mais recente)

NUNCA leia/renove token fora daqui. Todo o resto do projeto chama get_valid_access_token(db).
"""

from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from config.settings import settings
from database.models import OAuthToken

PRODUCT = "crm"

# Margem de segurança: renova antes de bater no limite exato de expiração
_EXPIRY_SAFETY_MARGIN = timedelta(minutes=5)


def get_authorization_url() -> str:
    """URL para o usuário abrir no navegador e autorizar o app no RD Station."""
    return (
        f"{settings.rd_auth_dialog_url}"
        f"?response_type=code"
        f"&client_id={settings.rd_crm_client_id}"
        f"&redirect_uri={settings.rd_crm_redirect_uri}"
    )


def exchange_code_for_token(db: Session, code: str) -> OAuthToken:
    """Primeira troca: authorization code -> access_token + refresh_token.

    IMPORTANTE: o endpoint de token do RD Station espera o corpo em
    application/x-www-form-urlencoded (nao JSON) -- httpx faz isso
    automaticamente quando passamos `data=` em vez de `json=`.
    """
    response = httpx.post(
        settings.rd_token_url,
        data={
            "client_id": settings.rd_crm_client_id,
            "client_secret": settings.rd_crm_client_secret,
            "code": code,
            "redirect_uri": settings.rd_crm_redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return _save_token(db, payload)


def _refresh_token(db: Session, refresh_token: str) -> OAuthToken:
    response = httpx.post(
        settings.rd_token_url,
        data={
            "client_id": settings.rd_crm_client_id,
            "client_secret": settings.rd_crm_client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return _save_token(db, payload)


def _save_token(db: Session, payload: dict) -> OAuthToken:
    expires_in = payload.get("expires_in", 7200)  # RD CRM: token expira em ~2h
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    token = db.query(OAuthToken).filter(OAuthToken.product == PRODUCT).one_or_none()
    if token is None:
        token = OAuthToken(product=PRODUCT)
        db.add(token)

    token.access_token = payload["access_token"]
    token.refresh_token = payload["refresh_token"]
    token.expires_at = expires_at
    db.commit()
    db.refresh(token)
    return token


def get_valid_access_token(db: Session) -> str:
    """Retorna um access_token válido, renovando via refresh_token se necessário."""
    token = db.query(OAuthToken).filter(OAuthToken.product == PRODUCT).one_or_none()
    if token is None:
        raise RuntimeError(
            "Nenhum token do RD CRM encontrado. Autorize o app primeiro em "
            f"{settings.rd_crm_redirect_uri.rsplit('/', 1)[0]} (fluxo /auth/rd/login)."
        )

    now = datetime.now(timezone.utc)
    expires_at = token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if now >= (expires_at - _EXPIRY_SAFETY_MARGIN):
        token = _refresh_token(db, token.refresh_token)

    return token.access_token
