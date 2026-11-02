"""Manda a abertura para os leads de evento que ficaram sem mensagem.

Quando acontece: o template do evento ainda nao estava aprovado/configurado
(status `sem_template`) ou o Meta recusou o envio (status `falhou`). Os leads e os
cards ja existem -- falta so a mensagem.

NAO mexe em `incerto` (o envio deu timeout e pode ter chegado): reenviar sozinho
poderia mandar duas aberturas pra uma pessoa. Esses voce confere no WhatsApp e, se
a mensagem nao chegou, reenvia um a um com `--incluir-incerto`.

Uso:
    python -m scripts.reenviar_leads_evento              # so lista o que seria enviado
    python -m scripts.reenviar_leads_evento --enviar     # envia de verdade
    python -m scripts.reenviar_leads_evento --enviar --incluir-incerto
"""

import sys

from config.settings import settings
from database.connection import session_scope
from database.models import EventoLead
from whatsapp import evento


def main() -> None:
    enviar = "--enviar" in sys.argv
    incluir_incerto = "--incluir-incerto" in sys.argv

    status = [evento.STATUS_SEM_TEMPLATE, evento.STATUS_FALHOU]
    if incluir_incerto:
        status.append(evento.STATUS_INCERTO)

    with session_scope() as db:
        pendentes = (
            db.query(EventoLead).filter(EventoLead.status.in_(status)).order_by(EventoLead.created_at).all()
        )
        print(f"{len(pendentes)} lead(s) pendente(s) ({', '.join(status)}).\n")
        for lead in pendentes:
            print(f"  [{lead.status:12}] {lead.nome} ({lead.telefone}) -- captado por {lead.vendedor}")
            if lead.erro:
                print(f"                 ultimo erro: {lead.erro[:140]}")

        if not pendentes:
            return
        if not enviar:
            print("\n(simulacao -- rode com --enviar pra mandar de verdade)")
            return
        if not settings.whatsapp_agent_evento_template_name.strip():
            print("\nWHATSAPP_AGENT_EVENTO_TEMPLATE_NAME nao esta configurado -- nada enviado.")
            return

        print()
        for lead in pendentes:
            evento.reenviar(db, lead)
            print(f"  -> {lead.nome}: {lead.status}" + (f" ({lead.erro[:100]})" if lead.erro else ""))


if __name__ == "__main__":
    main()
