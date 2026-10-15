"""`Ramifica`: um ponto de entrada para as duas saídas de IA de um app.

- O **código vai ramificar** com a resposta? `decide`, `when`, `switch`, `rank` -> Jev
  (decisões tipadas; offline e grátis quando não há orçamento).
- Uma **pessoa vai ler** a resposta? `write` -> LLM (local/gratuita por padrão).
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ramifica.backends.base import RawAnswer, RawResult
from ramifica.backends.jev import JevBackend
from ramifica.backends.offline import OfflineBackend
from ramifica.budget import Budget
from ramifica.config import DAY, Settings
from ramifica.decision import (
    Band,
    Decision,
    Decisions,
    Policy,
    Source,
    choice_confidence,
    noul_confidence,
    score_confidence,
)
from ramifica.errors import BackendError, BudgetExceeded, NoWriterError
from ramifica.models import ALIASES, resolve_model
from ramifica.questions import Choice, Content, Noul, Question, Score, noul, to_wire
from ramifica.store import Store
from ramifica.writers import Writer, auto_writer

logger = logging.getLogger("ramifica")

RELEVANCE_LEVELS: tuple[str, ...] = (
    "Irrelevante: não ajuda a responder `query`.",
    "Pouco relevante: toca no assunto de `query`, mas não responde.",
    "Relevante: responde parte de `query`.",
    "Muito relevante: responde `query` diretamente.",
)


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _Missing()


@dataclass(frozen=True)
class Case:
    """Um ramo de `switch`: descrição para o modelo e o que fazer quando escolhido.

    `then` pode ser um valor ou uma função (com ou sem o argumento `Decision`).
    Sem `then`, o `switch` devolve o próprio rótulo.
    """

    description: Content | None = None
    then: Any = MISSING


@dataclass(frozen=True)
class Ranked:
    item: Any
    score: float
    index: int
    decision: Decision


class Ramifica:
    """Roteador de saídas de IA.

    Args:
        jev: `JevBackend`, `None` (nunca usar Jev) ou `"auto"` (usa se houver
            `TYPESAFE_API_KEY` e `typesafe-sdk` instalado).
        writer: LLM de texto, `None` ou `"auto"` (detectada no primeiro `write`).
        model: `latest` | `preview` | `newest` | `jev-X.Y.Z` (padrão: `RAMIFICA_JEV_MODEL` ou `latest`).
        policy: limiares de confiança padrão.
        budget: teto mensal para o Jev (padrão: `RAMIFICA_BUDGET_USD`, 0 = custo zero).
        store: `Store`, caminho do SQLite ou `":memory:"`.
        fallback: `"offline"` responde com o backend offline quando o Jev não pode ser usado
            (sem orçamento, fora do ar); `"raise"` levanta a exceção.
    """

    def __init__(
        self,
        *,
        jev: JevBackend | Literal["auto"] | None = "auto",
        writer: Writer | Literal["auto"] | None = "auto",
        model: str | None = None,
        policy: Policy | None = None,
        budget: Budget | None = None,
        store: Store | str | Path | None = None,
        offline: OfflineBackend | None = None,
        fallback: Literal["offline", "raise"] = "offline",
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        if jev == "auto":
            jev = JevBackend() if JevBackend.key_configured() and JevBackend.sdk_available() else None
            if jev is None and JevBackend.key_configured():
                logger.warning("TYPESAFE_API_KEY definida, mas o SDK falta: pip install 'ramifica[jev]'.")
        self.jev: JevBackend | None = jev
        self._writer: Writer | Literal["auto"] | None = writer
        self.model_spec = model or self.settings.model
        self.policy = policy or Policy()
        self.budget = budget or Budget(self.settings.budget_usd, self.settings.usd_per_mtok)
        self.store = store if isinstance(store, Store) else Store(store)
        self.offline = offline or OfflineBackend()
        if fallback not in ("offline", "raise"):
            raise ValueError("fallback deve ser 'offline' ou 'raise'.")
        self.fallback = fallback
        self._warned: set[str] = set()

    # ------------------------------------------------------------------
    # Decisões (o código consome)
    # ------------------------------------------------------------------

    def decide(
        self,
        state: Any,
        questions: Mapping[str, Question],
        *,
        model: str | None = None,
        policy: Policy | None = None,
        cache: bool = True,
    ) -> Decisions:
        """Responde perguntas tipadas sobre `state` numa única requisição.

        Perguntas independentes sobre o mesmo estado devem ir juntas: rodam em paralelo no
        Jev e pagam o custo fixo da requisição uma vez só.
        """
        questions = dict(questions)
        wire = to_wire(questions)
        policy = policy or self.policy

        if self.jev is None:
            return self._offline(state, questions, policy)

        model_name = self.resolve_model(model)
        ttl = self._cache_ttl(model_name)
        use_cache = cache and ttl != 0
        key = _cache_key(model_name, state, wire)
        if use_cache:
            hit = self.store.get(key)
            if hit is not None:
                raw = RawResult(
                    answers={n: RawAnswer.from_json(a) for n, a in hit["answers"].items()},
                    model=hit.get("model"),
                    input_tokens=0,
                )
                return _build(raw, questions, policy, source="cache")

        estimated = self.budget.estimate_tokens(state, wire)
        try:
            self.budget.check(estimated, self.store.spent()[0])
        except BudgetExceeded as error:
            if self.fallback == "raise":
                raise
            self._warn_once("budget", f"{error} Usando o modo offline.")
            return self._offline(state, questions, policy)

        try:
            raw = self.jev.evaluate(state, questions, model=model_name)
        except BackendError as error:
            if self.fallback == "raise":
                raise
            logger.warning("%s Usando o modo offline.", error)
            return self._offline(state, questions, policy)

        tokens = raw.input_tokens if raw.input_tokens is not None else estimated
        cost = self.budget.cost(tokens)
        self.store.record(cost, tokens)  # cobrado mesmo que a resposta venha incompleta
        decisions = _build(raw, questions, policy, source="jev", cost_usd=cost)
        if use_cache:
            self.store.put(
                key,
                {"model": raw.model, "answers": {n: a.to_json() for n, a in raw.answers.items()}},
                ttl=ttl,
            )
        return decisions

    def ask(self, state: Any, question: Question, **kwargs: Any) -> Decision:
        """Uma pergunta só. Prefira `decide` quando houver várias sobre o mesmo estado."""
        return self.decide(state, {"answer": question}, **kwargs)["answer"]

    def when(
        self,
        state: Any,
        condition: str | Noul,
        then: Any = True,
        otherwise: Any = False,
        *,
        uncertain: Any = MISSING,
        **kwargs: Any,
    ) -> Any:
        """`if` semântico: `then` se a condição vale, `otherwise` se não.

        Se `uncertain` for passado, ele é usado quando a confiança cai na faixa LOW.
        `then`/`otherwise`/`uncertain` podem ser valores ou funções.
        """
        question = condition if isinstance(condition, Noul) else noul(condition)
        decision = self.ask(state, question, **kwargs)
        if decision.band is Band.LOW and uncertain is not MISSING:
            return _call(uncertain, decision)
        return _call(then if decision.yes else otherwise, decision)

    def switch(
        self,
        state: Any,
        instructions: Content,
        cases: Mapping[str, Case | Any],
        *,
        default: Any = MISSING,
        **kwargs: Any,
    ) -> Any:
        """`match` semântico: escolhe um ramo e executa/devolve o que ele aponta.

        `cases` mapeia rótulo -> `Case(descrição, then)` ou direto -> valor/função.
        Com `default`, confiança LOW cai no padrão em vez de arriscar o melhor palpite.
        """
        if not cases:
            raise ValueError("switch precisa de pelo menos um caso.")
        options = {
            label: case.description if isinstance(case, Case) else None for label, case in cases.items()
        }
        decision = self.ask(state, Choice(instructions, options), **kwargs)
        if decision.band is Band.LOW and default is not MISSING:
            return _call(default, decision)
        case = cases.get(str(decision.value), MISSING)
        if case is MISSING:
            if default is MISSING:
                raise BackendError(f"Opção desconhecida retornada: {decision.value!r}")
            return _call(default, decision)
        if isinstance(case, Case):
            return decision.value if case.then is MISSING else _call(case.then, decision)
        return _call(case, decision)

    def rank(
        self,
        query: Any,
        items: Sequence[Any],
        *,
        instructions: Content = "Quão relevante é este candidato para `query`?",
        levels: Sequence[Content] = RELEVANCE_LEVELS,
        top_k: int | None = None,
        **kwargs: Any,
    ) -> list[Ranked]:
        """Ordena `items` por relevância para `query` com um Score comparável por item."""
        items = list(items)
        if not items:
            return []
        state = {"query": query, "candidates": items}
        questions: dict[str, Question] = {
            f"c{i}": Score(f"{instructions} Candidato avaliado: `candidates[{i}]`.", list(levels))
            for i in range(len(items))
        }
        decisions = self.decide(state, questions, **kwargs)
        ranked = [
            Ranked(item=item, score=float(decisions[f"c{i}"].value), index=i, decision=decisions[f"c{i}"])
            for i, item in enumerate(items)
        ]
        ranked.sort(key=lambda r: r.score, reverse=True)
        return ranked[:top_k] if top_k is not None else ranked

    # ------------------------------------------------------------------
    # Texto (uma pessoa lê)
    # ------------------------------------------------------------------

    @property
    def writer(self) -> Writer | None:
        if self._writer == "auto":
            self._writer = auto_writer()
        return self._writer

    def write(
        self,
        prompt: str,
        *,
        context: Any = None,
        system: str | None = None,
        max_tokens: int = 800,
        temperature: float = 0.3,
    ) -> str:
        """Gera texto livre para uma pessoa ler. Nunca use isto para decidir um `if`."""
        writer = self.writer
        if writer is None:
            raise NoWriterError(
                "Nenhuma LLM de texto configurada. Opções gratuitas: rode o Ollama localmente "
                "(https://ollama.com) ou defina RAMIFICA_LLM_BASE_URL e RAMIFICA_LLM_MODEL para um "
                "endpoint compatível com OpenAI."
            )
        if context is not None:
            rendered = (
                context if isinstance(context, str) else json.dumps(context, ensure_ascii=False, indent=2)
            )
            prompt = f"{prompt}\n\nContexto:\n{rendered}"
        return writer.write(prompt, system=system, max_tokens=max_tokens, temperature=temperature)

    # ------------------------------------------------------------------
    # Async (mesma semântica, sem bloquear o event loop)
    # ------------------------------------------------------------------

    async def adecide(self, state: Any, questions: Mapping[str, Question], **kwargs: Any) -> Decisions:
        return await asyncio.to_thread(self.decide, state, questions, **kwargs)

    async def aask(self, state: Any, question: Question, **kwargs: Any) -> Decision:
        return await asyncio.to_thread(self.ask, state, question, **kwargs)

    async def awrite(self, prompt: str, **kwargs: Any) -> str:
        return await asyncio.to_thread(self.write, prompt, **kwargs)

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    def resolve_model(self, model: str | None = None) -> str:
        return resolve_model(model or self.model_spec, backend=self.jev, store=self.store)

    def status(self) -> dict[str, Any]:
        """Retrato da configuração atual (usado por `ramifica doctor`)."""
        spent_usd, spent_tokens, calls = self.store.spent()
        writer = self._writer if self._writer != "auto" else None
        return {
            "jev": {
                "sdk_installed": JevBackend.sdk_available(),
                "sdk_version": JevBackend.sdk_version(),
                "api_key": JevBackend.key_configured(),
                "enabled": self.jev is not None and not self.budget.zero_cost,
                "model_spec": self.model_spec,
            },
            "budget": {
                "monthly_usd": self.budget.monthly_usd,
                "usd_per_mtok": self.budget.usd_per_mtok,
                "zero_cost": self.budget.zero_cost,
                "spent_usd_this_month": round(spent_usd, 6),
                "input_tokens_this_month": spent_tokens,
                "calls_this_month": calls,
            },
            "writer": getattr(writer, "name", None)
            if writer
            else ("auto" if self._writer == "auto" else None),
            "cache": {"path": self.store.path, "entries": self.store.cache_size()},
        }

    def close(self) -> None:
        if self.jev is not None:
            self.jev.close()
        self.store.close()

    def __enter__(self) -> Ramifica:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _offline(self, state: Any, questions: Mapping[str, Question], policy: Policy) -> Decisions:
        raw = self.offline.evaluate(state, questions, model=None)
        return _build(raw, questions, policy, source="offline")

    def _cache_ttl(self, model_name: str) -> float | None:
        """None = não expira (versão fixa); 0 = cache desligado; >0 = segundos."""
        days = self.settings.cache_ttl_days
        if days <= 0:
            return 0
        if model_name in ALIASES.values():
            return days * DAY
        return None

    def _warn_once(self, key: str, message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            logger.warning(message)


# ----------------------------------------------------------------------
# Auxiliares
# ----------------------------------------------------------------------


def _cache_key(model: str, state: Any, wire: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {"m": model, "s": state, "q": wire},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build(
    raw: RawResult,
    questions: Mapping[str, Question],
    policy: Policy,
    *,
    source: Source,
    cost_usd: float = 0.0,
) -> Decisions:
    decisions: dict[str, Decision] = {}
    for name, question in questions.items():
        answer = raw.answers.get(name)
        if answer is None:
            raise BackendError(f"O backend não respondeu a pergunta {name!r}.")
        if answer.kind != question.kind:
            raise BackendError(
                f"Pergunta {name!r} é {question.kind}, mas a resposta veio como {answer.kind}."
            )
        if answer.kind == "noul":
            p = float(answer.value)
            confidence = noul_confidence(p)
            decisions[name] = Decision(
                name=name,
                kind="noul",
                value=p > 0.5,
                confidence=confidence,
                band=policy.band(confidence),
                probabilities={"true": p, "false": 1.0 - p},
                source=source,
                model=raw.model,
                probability=p,
            )
        elif answer.kind == "choice":
            probabilities = {str(k): float(v) for k, v in answer.probabilities.items()}
            confidence = (
                answer.confidence if answer.confidence is not None else choice_confidence(probabilities)
            )
            label = str(answer.value)
            decisions[name] = Decision(
                name=name,
                kind="choice",
                value=label,
                confidence=confidence,
                band=policy.band(confidence),
                probabilities=probabilities,
                source=source,
                model=raw.model,
                probability=probabilities.get(label),
            )
        else:
            levels = {int(k): float(v) for k, v in answer.probabilities.items()}
            confidence = answer.confidence if answer.confidence is not None else score_confidence(levels)
            mode = max(levels, key=lambda level: levels[level]) if levels else None
            decisions[name] = Decision(
                name=name,
                kind="score",
                value=float(answer.value),
                confidence=confidence,
                band=policy.band(confidence),
                probabilities=levels,
                source=source,
                model=raw.model,
                probability=levels.get(mode) if mode is not None else None,
                level=mode,
                legend={int(k): v for k, v in answer.legend.items()},
            )
    return Decisions(
        items_=decisions, source=source, model=raw.model, input_tokens=raw.input_tokens, cost_usd=cost_usd
    )


def _call(target: Any, decision: Decision) -> Any:
    """Executa `target` se for função (passando a `Decision` se ela aceitar argumentos)."""
    if not callable(target):
        return target
    try:
        params = inspect.signature(target).parameters.values()
    except (TypeError, ValueError):
        return target(decision)
    accepts_arg = any(
        p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL) for p in params
    )
    fn: Callable[..., Any] = target
    return fn(decision) if accepts_arg else fn()
