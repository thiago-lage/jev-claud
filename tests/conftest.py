"""Fixtures compartilhadas. Nenhum teste usa rede externa nem gasta dinheiro."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import Any

import pytest
from typesafe_sdk import ChoiceAnswer, NoulAnswer, ScoreAnswer

from jev_claude import Budget, JevBackend, JevClaude


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    for name in list(os.environ):
        if name.startswith(("JEV_CLAUDE_", "TYPESAFE_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("JEV_CLAUDE_HOME", str(tmp_path / "home"))
    # Porta "discard": recusa a conexão na hora, então nunca detectamos um Ollama real.
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")


class FakeClient:
    """Imita `typesafe_sdk.TypeSafeClient` devolvendo os tipos de resposta reais do SDK."""

    def __init__(
        self,
        answers: Mapping[str, Any] | Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        *,
        model: str = "jev-1.13.0",
        input_tokens: int | None = 300,
        error: Exception | None = None,
        models: list[tuple[str, str]] | None = None,
    ) -> None:
        self.answers = answers or {}
        self.model = model
        self.input_tokens = input_tokens
        self.error = error
        self.calls: list[dict[str, Any]] = []
        listed = [SimpleNamespace(name=n, release_date=d, description=f"desc {n}") for n, d in (models or [])]
        self.models = SimpleNamespace(list=lambda: SimpleNamespace(models=tuple(listed)))
        self.closed = False

    def system_one(
        self, *, state: Any, questions: Mapping[str, Any], model: str | None = None, assets: Any = None
    ) -> Any:
        self.calls.append({"state": state, "questions": questions, "model": model, "assets": assets})
        if self.error is not None:
            raise self.error
        answers = self.answers(questions) if callable(self.answers) else self.answers
        usage = SimpleNamespace(input_tokens=self.input_tokens, output_tokens=0)
        return SimpleNamespace(model=self.model, usage=usage, answers=dict(answers))

    def close(self) -> None:
        self.closed = True


def noul_answer(p: float) -> NoulAnswer:
    return NoulAnswer(noul=p)


def choice_answer(probabilities: Mapping[str, float], confidence: float | None = None) -> ChoiceAnswer:
    best = max(probabilities, key=lambda k: probabilities[k])
    if confidence is None:
        n = len(probabilities)
        confidence = (probabilities[best] - 1 / n) / (1 - 1 / n)
    return ChoiceAnswer(choice=best, probabilities=dict(probabilities), confidence=confidence)


def score_answer(probabilities: list[float], legend: list[str], confidence: float = 0.9) -> ScoreAnswer:
    expected = sum(i * p for i, p in enumerate(probabilities))
    return ScoreAnswer(
        score=expected,
        legend=dict(enumerate(legend)),
        probabilities=dict(enumerate(probabilities)),
        confidence=confidence,
    )


@pytest.fixture
def make_jev() -> Callable[..., JevClaude]:
    created: list[JevClaude] = []

    def factory(client: FakeClient | None = None, *, budget: float = 1.0, **kwargs: Any) -> JevClaude:
        kwargs.setdefault("store", ":memory:")
        kwargs.setdefault("writer", None)
        kwargs.setdefault("strategy", "jev")  # testes da cascata pedem strategy="cascade"
        jev = JevBackend(client=client) if client is not None else None
        r = JevClaude(jev=jev, budget=Budget(budget), **kwargs)
        created.append(r)
        return r

    yield factory  # type: ignore[misc]
    for r in created:
        r.close()
