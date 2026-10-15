"""Contrato comum dos backends de decisão."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable

from jev_claud.decision import Kind
from jev_claud.questions import Question


@dataclass(frozen=True)
class RawAnswer:
    """Resposta crua de um backend, antes da política de confiança."""

    kind: Kind
    value: str | float | Mapping[str, float]  # choice: rótulo; noul: p(sim); score: grau; box: bordas
    probabilities: Mapping[str, float] = field(default_factory=dict)
    confidence: float | None = None  # None para noul (calculada a partir de p) e box sem confiança
    legend: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["probabilities"] = {str(k): v for k, v in self.probabilities.items()}
        data["legend"] = {str(k): v for k, v in self.legend.items()}
        return data

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> RawAnswer:
        return cls(
            kind=data["kind"],
            value=data["value"],
            probabilities=dict(data.get("probabilities") or {}),
            confidence=data.get("confidence"),
            legend=dict(data.get("legend") or {}),
        )


@dataclass(frozen=True)
class RawResult:
    answers: Mapping[str, RawAnswer]
    model: str | None = None
    input_tokens: int | None = None


@runtime_checkable
class Backend(Protocol):
    """Qualquer coisa que responda perguntas tipadas sobre um estado."""

    name: str
    paid: bool

    def evaluate(self, state: Any, questions: Mapping[str, Question], *, model: str | None) -> RawResult: ...
