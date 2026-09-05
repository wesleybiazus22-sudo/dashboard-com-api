"""Cria todas as tabelas no banco configurado em DATABASE_URL. Uso: python -m scripts.init_db"""

from sqlalchemy import text

from database import models  # noqa: F401 - garante que todos os models sejam registrados no Base
from database.connection import Base, engine

# `create_all` so cria TABELAS que ainda nao existem -- nunca adiciona coluna nova
# numa tabela ja existente (ver mesmo comentario em database/views_meta_ads.sql).
# Colunas adicionadas a um model depois da tabela ja estar em producao precisam de
# ALTER TABLE explicito aqui, senao o codigo novo (que espera a coluna) quebra
# contra um banco desatualizado.
_ALTERS = [
    "alter table crm_deals add column if not exists fbclid varchar",
]

if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        for stmt in _ALTERS:
            conn.execute(text(stmt))
    print("Tabelas criadas/atualizadas com sucesso.")
