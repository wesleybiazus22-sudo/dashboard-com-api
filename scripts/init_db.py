"""Cria todas as tabelas no banco configurado em DATABASE_URL. Uso: python -m scripts.init_db"""

from database import models  # noqa: F401 - garante que todos os models sejam registrados no Base
from database.connection import Base, engine

if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    print("Tabelas criadas/atualizadas com sucesso.")
