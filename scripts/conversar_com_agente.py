"""Harness de teste MANUAL do agente de atendimento, sem precisar de WhatsApp
real. Sempre roda com `modo_teste=True` (ver ingestion/llm/agent.py) -- nenhuma
ferramenta do agente mexe no CRM de verdade, mesmo que o modelo "decida" usar.

Cada chamada deste script e UM turno da conversa; o historico fica salvo num
JSON entre chamadas, pra a conversa manter contexto de um turno pro outro.

Uso:
    python -m scripts.conversar_com_agente "mensagem do lead" --historico caminho.json
    python -m scripts.conversar_com_agente --reset --historico caminho.json   # zera a conversa
"""

import argparse
import json
import sys
from pathlib import Path

from database.connection import session_scope
from ingestion.llm.agent import conversar

# Console do Windows por padrao usa cp1252, que nao cobre emoji -- o agente
# responde em tom de WhatsApp e pode usar emoji naturalmente. Reconfigura pra
# UTF-8 pra nao quebrar o print (silencioso em qualquer SO onde ja for UTF-8).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mensagem", nargs="?", default=None)
    parser.add_argument("--historico", required=True, help="Caminho do JSON de historico (criado se nao existir)")
    parser.add_argument("--reset", action="store_true", help="Zera o historico antes de comecar (ou so zera, se nao passar mensagem)")
    args = parser.parse_args()

    caminho = Path(args.historico)

    historico: list[dict] = []
    if caminho.exists() and not args.reset:
        historico = json.loads(caminho.read_text(encoding="utf-8"))

    if args.mensagem is None:
        if args.reset:
            caminho.write_text("[]", encoding="utf-8")
            print("Historico zerado.")
        return

    with session_scope() as db:
        resposta, historico_atualizado = conversar(db, historico, args.mensagem, modo_teste=True)

    caminho.write_text(json.dumps(historico_atualizado, ensure_ascii=False, indent=2), encoding="utf-8")
    print(resposta)


if __name__ == "__main__":
    main()
