"""Esforço: quanto trabalho cada tarefa de texto merece, no LLM da sessão.

Espelha o seletor de esforço do Claude. O esforço controla só o tamanho dos blocos e a
profundidade do trabalho; o modelo é sempre o LLM da sessão em que o jev-claud está
instalado (não há seletor de modelo próprio, nem modelo local).

| Esforço   | Blocos                         | Junção                  | Revisão |
| --------- | ------------------------------ | ----------------------- | ------- |
| baixo     | grandes, sem sobreposição      | curta                   | não     |
| medio     | médios, pouca sobreposição     | média                   | não     |
| alto      | menores, mais sobreposição     | longa                   | não     |
| extra     | pequenos, mais sobreposição    | longa                   | não     |
| max       | os menores, mais sobreposição  | a mais longa            | não     |
| ultracode | como `max`                     | a mais longa            | sim     |

A revisão do `ultracode` é uma passada a mais, que confere o resumo final contra os
resumos parciais e tira o que não estiver apoiado neles.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Effort = Literal["baixo", "medio", "alto", "extra", "max", "ultracode"]

EFFORTS: tuple[Effort, ...] = ("baixo", "medio", "alto", "extra", "max", "ultracode")
DEFAULT_EFFORT: Effort = "medio"

_ALIASES: dict[str, Effort] = {
    "low": "baixo",
    "medium": "medio",
    "médio": "medio",
    "high": "alto",
    "xhigh": "extra",
    "máx": "max",
    "maximo": "max",
    "máximo": "max",
}


@dataclass(frozen=True)
class EffortPlan:
    """Como um esforço quebra o texto em blocos e quão fundo vai a junção."""

    chunk_chars: int
    """Tamanho máximo de cada bloco (caracteres)."""
    overlap_chars: int
    """Quanto cada bloco repete do anterior, para não perder o que caiu na emenda."""
    part_tokens: int
    """Limite de resposta por bloco."""
    final_tokens: int
    """Limite de resposta da junção final."""
    verify: bool = False
    """Revisa o resumo final contra os parciais, tirando o que não estiver apoiado neles."""


PLANS: dict[Effort, EffortPlan] = {
    "baixo": EffortPlan(9_000, 0, 200, 450),
    "medio": EffortPlan(6_000, 300, 300, 700),
    "alto": EffortPlan(4_000, 500, 400, 1_000),
    "extra": EffortPlan(3_000, 500, 450, 1_200),
    "max": EffortPlan(2_500, 600, 500, 1_400),
    "ultracode": EffortPlan(2_500, 600, 500, 1_400, verify=True),
}

WRITE_TOKENS: dict[Effort, int] = {
    "baixo": 400,
    "medio": 800,
    "alto": 1_200,
    "extra": 1_600,
    "max": 2_000,
    "ultracode": 2_000,
}
"""Limite de resposta de um `write` em cada esforço (quando a chamada não define `max_tokens`)."""


def normalize_effort(effort: str | None) -> Effort:
    """Um dos `EFFORTS`. Aceita também `low`, `medium`, `high`, `xhigh`, `médio`, `máx` e `máximo`."""
    if effort is None or not str(effort).strip():
        return DEFAULT_EFFORT
    key = str(effort).strip().lower()
    key = _ALIASES.get(key, key)
    for name in EFFORTS:
        if key == name:
            return name
    raise ValueError(f"Esforço deve ser {', '.join(EFFORTS)}; recebido {effort!r}.")


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
