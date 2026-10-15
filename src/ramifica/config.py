"""Configuração via variáveis de ambiente (todas opcionais)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from ramifica.budget import USD_PER_MTOK

DAY = 24 * 3600


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw.replace(",", "."))
    except ValueError as error:
        raise ValueError(f"{name} deve ser um número, recebido {raw!r}.") from error


@dataclass(frozen=True)
class Settings:
    budget_usd: float = 0.0
    """`RAMIFICA_BUDGET_USD`: teto mensal para o Jev. 0 = custo zero (só offline)."""
    usd_per_mtok: float = USD_PER_MTOK
    """`RAMIFICA_JEV_USD_PER_MTOK`: preço por milhão de tokens de entrada."""
    model: str = "latest"
    """`RAMIFICA_JEV_MODEL`: latest | preview | newest | jev-X.Y.Z."""
    cache_ttl_days: float = 7.0
    """`RAMIFICA_CACHE_TTL_DAYS`: validade do cache para aliases (versões fixas não expiram). 0 desliga."""

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            budget_usd=_float("RAMIFICA_BUDGET_USD", 0.0),
            usd_per_mtok=_float("RAMIFICA_JEV_USD_PER_MTOK", USD_PER_MTOK),
            model=os.environ.get("RAMIFICA_JEV_MODEL", "").strip() or "latest",
            cache_ttl_days=_float("RAMIFICA_CACHE_TTL_DAYS", 7.0),
        )
