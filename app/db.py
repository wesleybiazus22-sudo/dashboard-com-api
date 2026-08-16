"""Conexao compartilhada do dashboard Streamlit com o mesmo Postgres da API
(reaproveita database.connection, que ja le DATABASE_URL do .env)."""

import pandas as pd
import streamlit as st
from sqlalchemy import text

from database.connection import engine


@st.cache_data(ttl=300)
def query(sql: str, params: dict | None = None) -> pd.DataFrame:
    """Roda uma query e devolve um DataFrame. Cache de 5 min -- os dados do CRM nao
    mudam segundo a segundo, e isso evita bater no banco a cada interacao de filtro."""
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})
