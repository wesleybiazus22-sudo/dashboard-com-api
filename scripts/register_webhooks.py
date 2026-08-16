"""
Registra os webhooks crm_deal_created/crm_deal_updated no RD Station CRM, apontando
para a API publicada (Render). Sem isso, o processor de webhooks
(webhooks/processor.py) nunca roda de verdade -- e sdr_owner_rd_id/closer_owner_rd_id
(a base da divisao SDR x Closer) ficam vazios, porque essa logica so e aplicada
quando um evento chega via webhook, nao durante a sincronizacao normal.

Idempotente: lista os webhooks ja cadastrados antes de criar, pra nao duplicar se
rodar mais de uma vez.

Uso: python -m scripts.register_webhooks
"""

from urllib.parse import urlparse

from config.settings import settings
from database.connection import session_scope
from ingestion.rd_crm.client import RDCrmClient

EVENTS = ["crm_deal_created", "crm_deal_updated"]


def _public_base_url() -> str:
    parsed = urlparse(settings.rd_crm_redirect_uri)
    return f"{parsed.scheme}://{parsed.netloc}"


if __name__ == "__main__":
    webhook_url = f"{_public_base_url()}/webhooks/rd?token={settings.rd_webhook_token}"
    print(f"URL de webhook: {webhook_url}")

    with session_scope() as db:
        client = RDCrmClient(db)

        existing = list(client.paginate("/webhooks"))
        existing_events = {
            item.get("event_name")
            for item in existing
            if item.get("url") == webhook_url
        }

        for event in EVENTS:
            if event in existing_events:
                print(f"  {event}: ja cadastrado, pulando.")
                continue

            response = client.post(
                "/webhooks",
                json={"data": {"event_name": event, "url": webhook_url, "http_method": "POST"}},
            )
            webhook_id = response.get("data", {}).get("id", "?")
            print(f"  {event}: criado (id={webhook_id})")

    print("Concluido.")
