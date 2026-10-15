"""Perguntas tipadas: o que o código quer decidir.

Cada pergunta vira exatamente um primitivo do TypeSafe System One:

- `Choice` (escolha uma opção de um conjunto definido),
- `Noul` (probabilidade de uma condição ser verdadeira),
- `Score` (posição ao longo de níveis ordenados).

As instruções e critérios podem ser texto, objetos JSON ou listas, como na API.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeAlias

from ramifica.errors import QuestionError

Content: TypeAlias = "str | Mapping[str, Any] | Sequence[Any]"

MAX_CHOICE_OPTIONS = 255
MIN_SCORE_LEVELS = 2
MAX_SCORE_LEVELS = 10


@dataclass(frozen=True)
class Choice:
    """Escolhe uma opção. `options` mapeia rótulo -> descrição (ou `None`)."""

    instructions: Content
    options: Mapping[str, Content | None]

    kind = "choice"

    def __post_init__(self) -> None:
        if not self.options:
            raise QuestionError("Choice precisa de pelo menos uma opção.")
        if len(self.options) > MAX_CHOICE_OPTIONS:
            raise QuestionError(f"Choice aceita no máximo {MAX_CHOICE_OPTIONS} opções.")
        if any(not isinstance(label, str) or not label for label in self.options):
            raise QuestionError("Rótulos de Choice devem ser strings não vazias.")

    def to_wire(self) -> dict[str, Any]:
        return {"type": "choice", "instructions": self.instructions, "criteria": dict(self.options)}


@dataclass(frozen=True)
class Noul:
    """Condição sim/não. `yes`/`no` descrevem cada desfecho (opcionais)."""

    instructions: Content | None
    yes: Content | None = None
    no: Content | None = None

    kind = "noul"

    def __post_init__(self) -> None:
        if self.instructions is None and self.yes is None and self.no is None:
            raise QuestionError("Noul precisa de instruções ou de pelo menos um desfecho descrito.")

    def to_wire(self) -> dict[str, Any]:
        wire: dict[str, Any] = {"type": "noul"}
        if self.instructions is not None:
            wire["instructions"] = self.instructions
        if self.yes is not None or self.no is not None:
            wire["criteria"] = {"true": self.yes, "false": self.no}
        return wire


@dataclass(frozen=True)
class Score:
    """Grau ao longo de níveis ordenados, do índice 0 (menor) ao último (maior)."""

    instructions: Content
    levels: Sequence[Content]

    kind = "score"

    def __post_init__(self) -> None:
        if isinstance(self.levels, (str, bytes)):
            raise QuestionError("Score.levels deve ser uma lista de níveis, não uma string.")
        if not MIN_SCORE_LEVELS <= len(self.levels) <= MAX_SCORE_LEVELS:
            raise QuestionError(f"Score precisa de {MIN_SCORE_LEVELS} a {MAX_SCORE_LEVELS} níveis.")

    def to_wire(self) -> dict[str, Any]:
        return {"type": "score", "instructions": self.instructions, "criteria": list(self.levels)}


Question: TypeAlias = "Choice | Noul | Score"


def choice(instructions: Content, options: Mapping[str, Content | None] | Sequence[str]) -> Choice:
    """Atalho para `Choice`. Aceita uma lista de rótulos ou um dicionário rótulo -> descrição.

    Escreva descrições com os termos-chave de cada opção: ajudam o Jev e o modo offline.
    """
    if isinstance(options, Mapping):
        return Choice(instructions, dict(options))
    if isinstance(options, (str, bytes)):
        raise QuestionError("options deve ser uma lista de rótulos ou um dicionário, não uma string.")
    return Choice(instructions, dict.fromkeys(options))


def noul(instructions: Content | None, *, yes: Content | None = None, no: Content | None = None) -> Noul:
    """Atalho para `Noul`."""
    return Noul(instructions, yes, no)


def score(instructions: Content, levels: Sequence[Content]) -> Score:
    """Atalho para `Score`."""
    if isinstance(levels, (str, bytes)):
        raise QuestionError("levels deve ser uma lista de níveis, não uma string.")
    return Score(instructions, list(levels))


def to_wire(questions: Mapping[str, Question]) -> dict[str, dict[str, Any]]:
    """Converte perguntas para o formato JSON da API do TypeSafe."""
    if not questions:
        raise QuestionError("Pelo menos uma pergunta é obrigatória.")
    return {name: question.to_wire() for name, question in questions.items()}
