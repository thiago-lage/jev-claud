"""Variáveis de ambiente do jev-claud, com os nomes antigos como reserva.

O projeto se chamava jev-claude até a 0.6: `JEV_CLAUDE_BUDGET_USD` continua valendo quando
`JEV_CLAUD_BUDGET_USD` não está definida, para nenhuma configuração existente se perder.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

PREFIX = "JEV_CLAUD_"
OLD_PREFIX = "JEV_CLAUDE_"


def getenv(name: str, default: str = "", env: Mapping[str, str] | None = None) -> str:
    """Valor de `name` (ex.: `JEV_CLAUD_HOME`), ou do nome antigo equivalente, ou `default`."""
    source = os.environ if env is None else env
    value = source.get(name, "")
    if not value.strip() and name.startswith(PREFIX):
        value = source.get(OLD_PREFIX + name[len(PREFIX) :], "")
    return value if value.strip() else default
