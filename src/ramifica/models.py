"""Escolha do modelo Jev, com atualização automática.

Especificações aceitas (argumento `model` ou `RAMIFICA_JEV_MODEL`):

- `latest` / `stable` (padrão) -> `jev-latest`: a release estável mais recente; a TypeSafe
  move o alias a cada lançamento, então você recebe updates sem mudar código.
- `preview` -> `jev-preview`: a release mais nova, oficial ou não.
- `newest` -> consulta a lista de modelos da conta e fixa a versão numerada mais nova
  (resultado guardado por 24 h). Útil para ter o modelo novo *e* cache estável.
- qualquer outro valor (ex.: `jev-1.13.0`) -> versão fixa, para resultados reprodutíveis.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ramifica.errors import BackendError

if TYPE_CHECKING:
    from ramifica.backends.jev import JevBackend, ModelInfo
    from ramifica.store import Store

ALIASES = {"latest": "jev-latest", "stable": "jev-latest", "preview": "jev-preview"}
NEWEST_TTL_SECONDS = 24 * 3600
_NEWEST_KEY = "jev.newest"
_VERSIONED = re.compile(r"^jev-(\d+)\.(\d+)\.(\d+)$")


def newest_versioned(models: list[ModelInfo]) -> str | None:
    """Maior versão `jev-X.Y.Z` (desempate pela data de lançamento)."""
    best: tuple[tuple[int, int, int], str, str] | None = None
    for info in models:
        match = _VERSIONED.match(info.name)
        if not match:
            continue
        version = (int(match[1]), int(match[2]), int(match[3]))
        candidate = (version, info.release_date, info.name)
        if best is None or candidate > best:
            best = candidate
    return best[2] if best else None


def resolve_model(spec: str | None, *, backend: JevBackend | None, store: Store | None) -> str:
    spec = (spec or "latest").strip()
    if spec in ALIASES:
        return ALIASES[spec]
    if spec != "newest":
        return spec
    if store is not None:
        cached = store.meta_get(_NEWEST_KEY, max_age=NEWEST_TTL_SECONDS)
        if cached:
            return cached
    if backend is None:
        return ALIASES["preview"]
    try:
        newest = newest_versioned(backend.list_models())
    except BackendError:
        return ALIASES["preview"]
    if newest is None:
        return ALIASES["preview"]
    if store is not None:
        store.meta_put(_NEWEST_KEY, newest)
    return newest


def forget_newest(store: Store) -> None:
    store.meta_delete(_NEWEST_KEY)
