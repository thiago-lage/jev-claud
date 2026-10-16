"""Esforço: quanto trabalho, e qual modelo local, cada tarefa de texto merece.

Espelha o seletor de esforço do Claude, mas na camada grátis e offline: o texto grande é
quebrado em blocos, os blocos vão a um modelo local rápido e a junção final a um modelo mais
capaz. Tudo roda na LLM local (Ollama por padrão): nada sai da máquina, nada é cobrado.

| Esforço | Blocos                         | Modelo dos blocos | Modelo da junção |
| ------- | ------------------------------ | ----------------- | ---------------- |
| baixo   | grandes, sem sobreposição      | rápido            | rápido           |
| medio   | médios, pouca sobreposição     | rápido            | padrão           |
| alto    | menores, mais sobreposição     | padrão            | forte            |

Os modelos de cada camada vêm de `JEV_CLAUD_OLLAMA_MODEL_FAST` (rápido),
`JEV_CLAUD_OLLAMA_MODEL` (padrão) e `JEV_CLAUD_OLLAMA_MODEL_STRONG` (forte). Camada sem modelo
definido usa o padrão, então nada quebra sem configuração extra.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Effort = Literal["baixo", "medio", "alto"]
Tier = Literal["rapido", "padrao", "forte"]

EFFORTS: tuple[Effort, ...] = ("baixo", "medio", "alto")
TIERS: tuple[Tier, ...] = ("rapido", "padrao", "forte")
DEFAULT_EFFORT: Effort = "medio"

_ALIASES: dict[str, Effort] = {
    "low": "baixo",
    "medium": "medio",
    "médio": "medio",
    "high": "alto",
}


@dataclass(frozen=True)
class EffortPlan:
    """Como um esforço quebra e distribui o trabalho entre os modelos locais."""

    chunk_chars: int
    """Tamanho máximo de cada bloco enviado à LLM (caracteres)."""
    overlap_chars: int
    """Quanto cada bloco repete do anterior, para não perder o que caiu na emenda."""
    part_tokens: int
    """Limite de resposta por bloco."""
    final_tokens: int
    """Limite de resposta da junção final."""
    part_tier: Tier
    final_tier: Tier


PLANS: dict[Effort, EffortPlan] = {
    "baixo": EffortPlan(9_000, 0, 200, 450, "rapido", "rapido"),
    "medio": EffortPlan(6_000, 300, 300, 700, "rapido", "padrao"),
    "alto": EffortPlan(4_000, 500, 400, 1_000, "padrao", "forte"),
}

WRITE_TIER: dict[Effort, Tier] = {"baixo": "rapido", "medio": "padrao", "alto": "forte"}
"""Camada de modelo usada por um `write` simples em cada esforço."""


def normalize_effort(effort: str | None) -> Effort:
    """`baixo` | `medio` | `alto` (aceita também `low`, `medium`, `high` e `médio`)."""
    if effort is None or not str(effort).strip():
        return DEFAULT_EFFORT
    key = str(effort).strip().lower()
    key = _ALIASES.get(key, key)
    for name in EFFORTS:
        if key == name:
            return name
    raise ValueError(f"Esforço deve ser baixo, medio ou alto, recebido {effort!r}.")


def effort_plan(effort: str | None) -> EffortPlan:
    return PLANS[normalize_effort(effort)]


def split_text(text: str, chunk_chars: int, overlap_chars: int = 0) -> list[str]:
    """Quebra `text` em blocos de até `chunk_chars`, cortando em parágrafo, linha, frase ou espaço.

    Cada bloco começa `overlap_chars` antes do fim do anterior (alinhado a um espaço), para o
    que estava na emenda aparecer inteiro em pelo menos um bloco. Texto vazio devolve `[]`.
    """
    if chunk_chars <= 0:
        raise ValueError("chunk_chars deve ser positivo.")
    overlap_chars = min(max(overlap_chars, 0), chunk_chars // 2)
    chunks: list[str] = []
    start, size = 0, len(text)
    while start < size:
        end = min(start + chunk_chars, size)
        if end < size:
            end = _cut(text, start, end)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= size:
            break
        start = _align(text, end - overlap_chars, end) if overlap_chars else end
    return chunks


def pack(parts: list[str], limit: int) -> list[list[str]]:
    """Agrupa `parts` em sequência, cada grupo com até `limit` caracteres (no mínimo uma parte)."""
    groups: list[list[str]] = []
    current: list[str] = []
    used = 0
    for part in parts:
        if current and used + len(part) > limit:
            groups.append(current)
            current, used = [], 0
        current.append(part)
        used += len(part)
    if current:
        groups.append(current)
    return groups


def _cut(text: str, start: int, end: int) -> int:
    """Melhor ponto de corte em `text[start:end]`, procurando nos últimos 40% da janela."""
    floor = start + (end - start) * 3 // 5
    for separator in ("\n\n", "\n", ". ", " "):
        index = text.rfind(separator, floor, end)
        if index != -1:
            return index + len(separator)
    return end


def _align(text: str, position: int, end: int) -> int:
    """Primeiro início de palavra em `text[position:end]`; sem espaço, começa em `end`."""
    index = text.find(" ", position, end)
    return index + 1 if index != -1 else end
