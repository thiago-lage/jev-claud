"""Configuração: variáveis de ambiente > arquivo `config.json` > padrões.

O arquivo fica em `JEV_CLAUDE_HOME` (ou no cache do usuário) e é escrito pelo
`jev-claude setup`. Variáveis de ambiente sempre têm prioridade sobre ele.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jev_claude.budget import USD_PER_MTOK
from jev_claude.store import default_home

DAY = 24 * 3600

CONFIG_KEYS = frozenset(
    {
        "budget_usd",
        "usd_per_mtok",
        "model",
        "cache_ttl_days",
        "typesafe_api_key",
        "ollama_model",
        "setup_version",
    }
)


def config_path() -> Path:
    return default_home() / "config.json"


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Não foi possível ler {path}: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"{path} deve conter um objeto JSON.")
    return {key: value for key, value in data.items() if key in CONFIG_KEYS}


def save_config(values: Mapping[str, Any], path: Path | None = None) -> Path:
    """Mescla `values` no arquivo (valor `None` remove a chave) e restringe as permissões."""
    unknown = set(values) - CONFIG_KEYS
    if unknown:
        raise ValueError(f"Chaves de configuração desconhecidas: {sorted(unknown)}")
    path = path or config_path()
    data = load_config(path)
    for key, value in values.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)  # o arquivo pode conter a chave do Jev
    return path


def _float(name: str, file_value: Any, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        if file_value is None:
            return default
        raw = str(file_value)
    try:
        return float(raw.replace(",", "."))
    except ValueError as error:
        raise ValueError(f"{name} deve ser um número, recebido {raw!r}.") from error


def _text(name: str, file_value: Any) -> str | None:
    value = os.environ.get(name, "").strip() or (str(file_value).strip() if file_value else "")
    return value or None


@dataclass(frozen=True)
class Settings:
    budget_usd: float = 0.0
    """`JEV_CLAUDE_BUDGET_USD`: teto mensal para o Jev. 0 = custo zero (só offline)."""
    usd_per_mtok: float = USD_PER_MTOK
    """`JEV_CLAUDE_USD_PER_MTOK`: preço por milhão de tokens de entrada."""
    model: str = "latest"
    """`JEV_CLAUDE_MODEL`: latest | preview | newest | jev-X.Y.Z."""
    cache_ttl_days: float = 7.0
    """`JEV_CLAUDE_CACHE_TTL_DAYS`: validade do cache para aliases (versões fixas não expiram). 0 desliga."""
    typesafe_api_key: str | None = field(default=None, repr=False)
    """`TYPESAFE_API_KEY`: chave do Jev."""
    ollama_model: str | None = None
    """`JEV_CLAUDE_OLLAMA_MODEL`: modelo do Ollama para texto (padrão `llama3.2`)."""

    @classmethod
    def from_env(cls) -> Settings:
        file = load_config()
        return cls(
            budget_usd=_float("JEV_CLAUDE_BUDGET_USD", file.get("budget_usd"), 0.0),
            usd_per_mtok=_float("JEV_CLAUDE_USD_PER_MTOK", file.get("usd_per_mtok"), USD_PER_MTOK),
            model=_text("JEV_CLAUDE_MODEL", file.get("model")) or "latest",
            cache_ttl_days=_float("JEV_CLAUDE_CACHE_TTL_DAYS", file.get("cache_ttl_days"), 7.0),
            typesafe_api_key=_text("TYPESAFE_API_KEY", file.get("typesafe_api_key")),
            ollama_model=_text("JEV_CLAUDE_OLLAMA_MODEL", file.get("ollama_model")),
        )
