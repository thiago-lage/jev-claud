"""Quanto o jev-claud economizou: tokens do Claude (contexto) e do Jev (cascata e cache).

São estimativas, e o texto diz isso ("~"): tokens do Claude ≈ caracteres / 4, a média
para texto e código. Nada aqui pode quebrar quem chama: falhas de disco são ignoradas.
"""

from __future__ import annotations

import contextlib
import sqlite3

from jev_claud.store import Store

CHARS_PER_TOKEN = 4

# Tipos de economia de contexto do Claude.
COMPACT = "compact"  # saídas de teste/build compactadas pelo hook
TOOLS = "tools"  # arquivos processados pelas ferramentas MCP em vez de lidos pelo Claude


def tokens_from_chars(chars: int) -> int:
    return max(0, chars) // CHARS_PER_TOKEN


def record(kind: str, saved_chars: int, store: Store | None = None) -> None:
    """Registra a economia; nunca levanta exceção (roda dentro de hooks e do MCP)."""
    tokens = tokens_from_chars(saved_chars)
    if tokens <= 0:
        return
    with contextlib.suppress(OSError, sqlite3.Error, ValueError):
        if store is not None:
            store.record_saving(kind, tokens)
            return
        own = Store()
        try:
            own.record_saving(kind, tokens)
        finally:
            own.close()


def human_tokens(tokens: int) -> str:
    """12400 -> "12,4 mil"; 1_250_000 -> "1,2 mi" (português do Brasil)."""
    if tokens < 1000:
        return str(tokens)
    if tokens < 1_000_000:
        value, unit = tokens / 1000, "mil"
    else:
        value, unit = tokens / 1_000_000, "mi"
    text = f"{value:.1f}" if value < 100 else f"{value:.0f}"
    return f"{text.rstrip('0').rstrip('.').replace('.', ',')} {unit}"


def summary(store: Store) -> str:
    """Uma linha para o rodapé do terminal, ou um convite quando ainda não há economia."""
    claude = sum(tokens for _, tokens in store.savings().values())
    jev = sum(saved for _, saved in store.routing().values())
    parts = []
    if claude:
        parts.append(f"~{human_tokens(claude)} tokens do Claude")
    if jev:
        parts.append(f"~{human_tokens(jev)} do Jev")
    if not parts:
        return "nenhuma economia registrada ainda este mês"
    return "economizou este mês: " + " · ".join(parts)
