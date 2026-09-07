"""Carrega/atualiza o conteudo curado de ingestion/llm/knowledge_base_seed.py
na tabela `knowledge_chunks`. Idempotente por `titulo` -- rodar de novo depois
de editar o conteudo do seed atualiza as linhas existentes, sem duplicar.

Uso: python -m scripts.load_knowledge_base
"""

from database.connection import session_scope
from database.models import KnowledgeChunk
from ingestion.llm.knowledge_base_seed import KNOWLEDGE_BASE

if __name__ == "__main__":
    with session_scope() as db:
        count = 0
        for item in KNOWLEDGE_BASE:
            obj = db.query(KnowledgeChunk).filter(KnowledgeChunk.titulo == item["titulo"]).one_or_none()
            if obj is None:
                obj = KnowledgeChunk(titulo=item["titulo"])
                db.add(obj)
            obj.categoria = item["categoria"]
            obj.conteudo = item["conteudo"]
            obj.fonte = item.get("fonte")
            count += 1
        db.commit()
    print(f"Base de conhecimento carregada: {count} pedaços.")
