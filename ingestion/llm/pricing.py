"""
Calcula e registra o custo de cada chamada ao Claude feita pelo agente de
atendimento. Grava em `llm_call_log` (ver database/models.py) -- essa peca
nasce ANTES do agente em si de proposito, pra o custo ja vir monitorado desde
a primeira chamada real, em vez de ser bolado depois por cima.

TABELA DE PRECOS -- precisa ser conferida periodicamente contra
https://www.anthropic.com/pricing (o valor aqui e o vigente no momento em que
este arquivo foi escrito; a Anthropic pode reajustar). Preco em USD por MILHAO
de tokens. Cache de prompt tem preco proprio, diferente do token normal de
input -- por isso as colunas separadas em `LlmCallLog`.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from database.models import LlmCallLog

# USD por 1 milhao de tokens. `cache_write` e o custo de ESCREVER no cache (mais
# caro que input normal); `cache_read` e bem mais barato que input normal --
# e o ganho de usar prompt caching.
#
# Conferido contra a tabela oficial vigente em 2026-09-18 (ver
# https://www.anthropic.com/pricing) -- a tabela anterior estava desatualizada
# (tinha o preco antigo do Sonnet 4.6 rotulado como "claude-sonnet-5", ~50%
# mais caro que o real, alem da chave do Haiku com sufixo de data que nao
# corresponde a nenhum model ID valido). cache_write = 1.25x o input, cache_read
# = 0.1x o input -- proporcao padrao da Anthropic pra cache efemero (TTL 5min).
_PRECOS_POR_MILHAO = {
    "claude-fable-5-1": {"input": 10.00, "output": 50.00, "cache_write": 12.50, "cache_read": 1.00},
    "claude-opus-5": {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-sonnet-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.20},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cache_write": 1.25, "cache_read": 0.10},
}


def calcular_custo_usd(
    model: str, *, input_tokens: int, output_tokens: int,
    cache_write_tokens: int = 0, cache_read_tokens: int = 0,
) -> float:
    """Calcula o custo em USD de uma chamada, dado o modelo e a contagem de
    tokens (que vem direto da resposta da API -- `usage.input_tokens` etc.).
    Modelo desconhecido nao quebra: usa o preco do Sonnet como aproximacao
    conservadora e deixa um sinal claro (custo aproximado) pra revisao manual."""
    precos = _PRECOS_POR_MILHAO.get(model, _PRECOS_POR_MILHAO["claude-sonnet-5"])
    return round(
        input_tokens * precos["input"] / 1_000_000
        + output_tokens * precos["output"] / 1_000_000
        + cache_write_tokens * precos["cache_write"] / 1_000_000
        + cache_read_tokens * precos["cache_read"] / 1_000_000,
        6,
    )


def registrar_chamada(
    db: Session, *, model: str, input_tokens: int, output_tokens: int,
    cache_write_tokens: int = 0, cache_read_tokens: int = 0,
    finalidade: str | None = None, phone_number: str | None = None, deal_rd_id: str | None = None,
) -> LlmCallLog:
    """Chame isso logo depois de cada resposta do Claude no agente, passando
    `response.usage.*` direto. Nao precisa `db.commit()` externo -- ja comita
    aqui, pra o registro de custo nunca ficar pendurado numa transacao que o
    resto do fluxo do agente ainda vai mexer."""
    custo = calcular_custo_usd(
        model, input_tokens=input_tokens, output_tokens=output_tokens,
        cache_write_tokens=cache_write_tokens, cache_read_tokens=cache_read_tokens,
    )
    registro = LlmCallLog(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_write_tokens=cache_write_tokens,
        cache_read_tokens=cache_read_tokens,
        custo_usd=custo,
        finalidade=finalidade,
        phone_number=phone_number,
        deal_rd_id=deal_rd_id,
        occurred_at=datetime.now(timezone.utc),
    )
    db.add(registro)
    db.commit()
    return registro
