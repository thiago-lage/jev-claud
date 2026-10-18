"""Backend offline: heurística lexical local, determinística e de custo zero.

Não é um modelo e não substitui o Jev. Serve para desenvolvimento, testes, CI e como rede
de segurança quando o orçamento é zero ou o Jev está indisponível. As confianças tendem a
ser baixas quando o texto não contém os termos das opções; deixe a `Policy` cuidar disso.

Dica: descreva cada opção com os termos que aparecem no texto real (ex.: "cobrança, fatura,
pagamento"). Isso melhora o modo offline e também ajuda o Jev.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from jev_claud.backends.base import RawAnswer, RawResult
from jev_claud.decision import choice_confidence, score_confidence
from jev_claud.errors import BackendError
from jev_claud.questions import Choice, Noul, Question, Score

MODEL_NAME = "offline-lexical-1"
_NEGATION = re.compile(r"\b(?:não|nao|nem|nunca|not|never|neither|no)\b|n['’]t\b", re.IGNORECASE)

_WORD = re.compile(r"[a-z0-9]+")
_PATH_REF = re.compile(r"`([^`]+)`")
_PATH_PART = re.compile(r"[^.\[\]]+|\[\d+\]")
_BETA = 4.0

_STOPWORDS = frozenset(
    """
    uma uns umas dos das nos nas por para pra com sem sobre entre que nao sim mas como mais menos
    muito pouco este esta isto esse essa isso aquele aquela ele ela eles elas eu voce voces meu
    minha seu sua aos foi ser sao estao tem ter qual quais quem quando onde porque the and but not
    yes are was were been this that these those what which who when where why how does did has
    have had can could should would will your our their its from than with without about for
    """.split()
)


class OfflineBackend:
    name = "offline"
    paid = False

    def evaluate(
        self, state: Any, questions: Mapping[str, Question], *, model: str | None = None
    ) -> RawResult:
        state_features = features(flatten(state))
        answers = {name: self._answer(state, state_features, q) for name, q in questions.items()}
        # Coincidência lexical não entende o escopo de uma negação. Abster-se evita
        # aceitar com alta confiança "não é cobrança, é um bug" na cascata.
        if _NEGATION.search(flatten(state)):
            answers = {
                name: replace(answer, value=0.5, confidence=None)
                if answer.kind == "noul"
                else replace(answer, confidence=0.0)
                for name, answer in answers.items()
            }
        return RawResult(answers=answers, model=MODEL_NAME, input_tokens=0)

    def _answer(self, state: Any, state_features: set[str], question: Question) -> RawAnswer:
        if isinstance(question, Choice):
            return _choice(state_features, question)
        if isinstance(question, Noul):
            return _noul(state_features, question)
        if isinstance(question, Score):
            return _score(state, state_features, question)
        if getattr(question, "kind", None) == "box":
            raise BackendError("Box exige o Jev (modelo de visão): o modo offline não enxerga imagens.")
        raise TypeError(f"Pergunta não suportada: {type(question).__name__}")

    def relevance(self, query: Any, text: Any) -> float:
        """Similaridade lexical em [0, 1] entre dois conteúdos."""
        return cosine(features(flatten(query)), features(flatten(text)))


# ---------------------------------------------------------------------------
# Primitivos
# ---------------------------------------------------------------------------


def _choice(state_features: set[str], question: Choice) -> RawAnswer:
    sims = {
        label: coverage(state_features, features(label.replace("_", " ") + " " + flatten(description)))
        for label, description in question.options.items()
    }
    probabilities = softmax(sims)
    best = max(probabilities, key=lambda label: probabilities[label])
    return RawAnswer(
        kind="choice", value=best, probabilities=probabilities, confidence=choice_confidence(probabilities)
    )


def _noul(state_features: set[str], question: Noul) -> RawAnswer:
    if question.yes is not None or question.no is not None:
        yes = coverage(state_features, features(flatten(question.yes))) if question.yes is not None else 0.0
        no = coverage(state_features, features(flatten(question.no))) if question.no is not None else 0.0
        if question.yes is not None and question.no is not None:
            p = sigmoid(_BETA * (yes - no))
        elif question.yes is not None:
            p = 0.5 + 0.45 * math.tanh(2 * yes)
        else:
            p = 0.5 - 0.45 * math.tanh(2 * no)
    else:
        overlap = coverage(state_features, features(flatten(question.instructions)))
        p = 0.5 + 0.45 * math.tanh(2 * overlap)
    return RawAnswer(kind="noul", value=p)


def _score(state: Any, state_features: set[str], question: Score) -> RawAnswer:
    n = len(question.levels)
    focus, context = _focus_and_context(state, question.instructions)
    if focus is not None:
        # A pergunta avalia um trecho específico do estado (ex.: `candidates[3]`) em relação ao
        # restante (ex.: `query`): quanto mais parecidos, mais alto o nível.
        relevance = cosine(features(flatten(context)), features(flatten(focus)))
        target = min(1.0, 2.0 * relevance) * (n - 1)
        weights = {str(i): -abs(i - target) for i in range(n)}
    else:
        weights = {
            str(i): coverage(state_features, features(flatten(level)))
            for i, level in enumerate(question.levels)
        }
    probabilities = softmax(weights)
    expected = sum(int(level) * p for level, p in probabilities.items())
    confidence = score_confidence({int(k): v for k, v in probabilities.items()})
    legend = {str(i): level for i, level in enumerate(question.levels)}
    return RawAnswer(
        kind="score", value=expected, probabilities=probabilities, confidence=confidence, legend=legend
    )


def _focus_and_context(state: Any, instructions: Any) -> tuple[Any | None, Any]:
    if not isinstance(state, Mapping):
        return None, state
    focus: list[Any] = []
    used_keys: set[str] = set()
    for path in _PATH_REF.findall(flatten(instructions)):
        parts = _PATH_PART.findall(path.strip())
        # Só um elemento específico (`candidates[3]`, `ticket.body`) é foco; uma referência de
        # topo como `query` descreve o contexto da comparação.
        if len(parts) < 2:
            continue
        found, value = resolve_path(state, path)
        if found:
            focus.append(value)
            used_keys.add(parts[0])
    if not focus or used_keys >= set(state):
        return None, state
    return focus, {key: value for key, value in state.items() if key not in used_keys}


# ---------------------------------------------------------------------------
# Utilidades de texto
# ---------------------------------------------------------------------------


def flatten(content: Any) -> str:
    """Junta todo o texto de um conteúdo (str, dict, list) em uma string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        return " ".join(flatten(value) for value in content.values())
    if isinstance(content, Sequence) and not isinstance(content, (bytes, bytearray)):
        return " ".join(flatten(item) for item in content)
    return str(content)


def features(text: str) -> set[str]:
    """Tokens normalizados (sem acento, minúsculos, prefixo de 5 letras, sem stopwords)."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return {token[:5] for token in _WORD.findall(plain) if len(token) >= 3 and token not in _STOPWORDS}


def coverage(text: set[str], target: set[str]) -> float:
    """Quanto de `target` aparece em `text`, com penalidade suave para alvos longos."""
    if not target:
        return 0.0
    return len(text & target) / math.sqrt(len(target))


def cosine(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / math.sqrt(len(a) * len(b))


def softmax(scores: Mapping[str, float], beta: float = _BETA) -> dict[str, float]:
    if not scores:
        return {}
    top = max(scores.values())
    exps = {key: math.exp(beta * (value - top)) for key, value in scores.items()}
    total = sum(exps.values())
    return {key: value / total for key, value in exps.items()}


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def resolve_path(state: Any, path: str) -> tuple[bool, Any]:
    """Resolve caminhos como `ticket.messages[0].text` dentro do estado."""
    current = state
    for part in _PATH_PART.findall(path.strip()):
        if part.startswith("["):
            index = int(part[1:-1])
            if not isinstance(current, Sequence) or isinstance(current, str) or index >= len(current):
                return False, None
            current = current[index]
        else:
            if not isinstance(current, Mapping) or part not in current:
                return False, None
            current = current[part]
    return True, current
