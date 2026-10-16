"""`JevClaud`: um ponto de entrada para as duas saídas de IA de um app.

- O **código vai ramificar** com a resposta? `decide`, `when`, `switch`, `rank` -> Jev
  (decisões tipadas; offline e grátis quando não há orçamento).

Com o Jev disponível, a estratégia padrão é a **cascata**: cache -> offline (grátis) ->
Jev só para as perguntas que o offline não resolveu com confiança, numa única requisição.
O óbvio sai de graça; o difícil continua com o Jev.
- Uma **pessoa vai ler** a resposta? `write` -> LLM (local/gratuita por padrão).
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from jev_claud import media
from jev_claud.backends.base import RawAnswer, RawResult
from jev_claud.backends.jev import JevBackend
from jev_claud.backends.offline import OfflineBackend
from jev_claud.budget import Budget
from jev_claud.config import DAY, STRATEGIES, Settings
from jev_claud.decision import (
    Band,
    Decision,
    Decisions,
    Policy,
    Region,
    Source,
    choice_confidence,
    noul_confidence,
    score_confidence,
)
from jev_claud.effort import TIERS, EffortPlan, Tier, effort_plan, pack, split_text
from jev_claud.errors import BackendError, BudgetExceeded, NoWriterError, WriterError
from jev_claud.models import ALIASES, resolve_model
from jev_claud.questions import Choice, Content, Noul, Question, Score, noul, to_wire
from jev_claud.store import Store
from jev_claud.writers import OpenAICompatibleWriter, Writer, auto_writer, is_local_url

logger = logging.getLogger("jev_claud")

Strategy = Literal["cascade", "jev"]

RELEVANCE_LEVELS: tuple[str, ...] = (
    "Irrelevante: não ajuda a responder `query`.",
    "Pouco relevante: toca no assunto de `query`, mas não responde.",
    "Relevante: responde parte de `query`.",
    "Muito relevante: responde `query` diretamente.",
)

SUMMARY_SYSTEM = "Você resume documentos com fidelidade, sem inventar nada. Responda em português."
DEFAULT_FOCUS = "Resuma os pontos principais em tópicos curtos."


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


class JevClaud:
    """Roteador de saídas de IA.

    Args:
        jev: `JevBackend`, `None` (nunca usar Jev) ou `"auto"` (usa se houver
            `TYPESAFE_API_KEY` e `typesafe-sdk` instalado).
        writer: LLM de texto, `None` ou `"auto"` (detectada no primeiro `write`).
        model: `latest` | `preview` | `newest` | `jev-X.Y.Z` (padrão: `JEV_CLAUD_MODEL` ou `latest`).
        policy: limiares de confiança padrão.
        budget: teto mensal para o Jev (padrão: `JEV_CLAUD_BUDGET_USD`, 0 = custo zero).
        store: `Store`, caminho do SQLite ou `":memory:"`.
        fallback: `"offline"` responde com o backend offline quando o Jev não pode ser usado
            (sem orçamento, fora do ar); `"raise"` levanta a exceção.
        strategy: `"cascade"` (padrão: offline primeiro, Jev só nas perguntas incertas) ou
            `"jev"` (toda pergunta vai ao Jev). Padrão: `JEV_CLAUD_STRATEGY`.
        cascade_accept: confiança mínima para aceitar a resposta offline na cascata
            (padrão: `JEV_CLAUD_CASCADE_ACCEPT`, 0,8). Suba para escalar mais ao Jev.

    Com `JEV_CLAUD_OFFLINE=1` (`Settings.offline`), nada sai da máquina: o Jev nunca é usado
    e o texto só vai a uma LLM local (esta máquina ou a rede local).
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
        strategy: Strategy | None = None,
        cascade_accept: float | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        # Validações antes de abrir o banco: um erro aqui não deixa conexão aberta.
        self.strategy: Strategy = strategy or self.settings.strategy  # type: ignore[assignment]
        if self.strategy not in STRATEGIES:
            raise ValueError(f"strategy deve ser {' ou '.join(STRATEGIES)}.")
        self.cascade_accept = self.settings.cascade_accept if cascade_accept is None else cascade_accept
        if not 0.0 <= self.cascade_accept <= 1.0:
            raise ValueError("cascade_accept deve estar entre 0 e 1.")
        if self.settings.offline:
            if jev not in (None, "auto"):
                logger.warning("JEV_CLAUD_OFFLINE=1: o backend do Jev passado será ignorado.")
            jev = None
        if jev == "auto":
            key = self.settings.typesafe_api_key
            has_key = JevBackend.key_configured(key)
            jev = JevBackend(api_key=key) if has_key and JevBackend.sdk_available() else None
            if jev is None and has_key:
                logger.warning("Chave do Jev configurada, mas o SDK falta: pip install 'jev-claud[jev]'.")
        self.jev: JevBackend | None = jev
        self._writer: Writer | Literal["auto"] | None = writer
        self._tier_writers: dict[Tier, Writer] = {}
        self._broken_tiers: set[Tier] = set()
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
        strategy: Strategy | None = None,
        escalate: Literal["each", "all"] = "each",
    ) -> Decisions:
        """Responde perguntas tipadas sobre `state` numa única requisição.

        Perguntas independentes sobre o mesmo estado devem ir juntas: rodam em paralelo no
        Jev e pagam o custo fixo da requisição uma vez só.

        Na cascata, `escalate="all"` manda todas ao Jev se alguma for incerta (útil quando
        as respostas serão comparadas entre si, como no `rank`).
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
                self.store.record_routing("cache", len(questions), self.budget.estimate_tokens(state, wire))
                return _build(raw, questions, policy, source="cache")

        offline_raw: RawResult | None = None
        accepted: list[str] = []
        if (strategy or self.strategy) == "cascade":
            offline_raw, accepted = self._triage(state, questions, policy)
            if escalate == "all" and len(accepted) < len(questions):
                accepted = []
            if len(accepted) == len(questions):
                assert offline_raw is not None
                self.store.record_routing("offline", len(questions), self.budget.estimate_tokens(state, wire))
                return _build(offline_raw, questions, policy, source="offline")

        pending = {name: q for name, q in questions.items() if name not in accepted}
        pending_wire = {name: wire[name] for name in pending}
        estimated = self.budget.estimate_tokens(state, pending_wire)
        try:
            self.budget.check(estimated, self.store.spent()[0])
            raw_jev = self.jev.evaluate(state, pending, model=model_name)
        except (BudgetExceeded, BackendError) as error:
            if self.fallback == "raise":
                raise
            if isinstance(error, BudgetExceeded):
                self._warn_once("budget", f"{error} Usando o modo offline.")
            else:
                logger.warning("%s Usando o modo offline.", error)
            self.store.record_routing("fallback", len(pending))
            return self._offline(state, questions, policy, raw=offline_raw, accepted=accepted)

        tokens = raw_jev.input_tokens if raw_jev.input_tokens is not None else estimated
        cost = self.budget.cost(tokens)
        self.store.record(cost, tokens)  # cobrado mesmo que a resposta venha incompleta
        saved = self.budget.estimate_tokens(state, wire) - estimated if accepted else 0
        self.store.record_routing("offline", len(accepted), saved)
        self.store.record_routing("jev", len(pending))

        answers = dict(raw_jev.answers)
        sources: dict[str, Source] = {}
        if accepted:
            assert offline_raw is not None
            for name in accepted:
                answers[name] = offline_raw.answers[name]
                sources[name] = "offline"
        raw = RawResult(answers=answers, model=raw_jev.model, input_tokens=raw_jev.input_tokens)
        decisions = _build(
            raw,
            questions,
            policy,
            source="mixed" if accepted else "jev",
            cost_usd=cost,
            sources=sources,
            escalated=tuple(pending) if accepted else (),
        )
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
        # Notas de backends diferentes não são comparáveis: ou todas offline, ou todas no Jev.
        kwargs.setdefault("escalate", "all")
        decisions = self.decide(state, questions, **kwargs)
        ranked = [
            Ranked(item=item, score=decisions[f"c{i}"].number, index=i, decision=decisions[f"c{i}"])
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
            self._writer = auto_writer(self.settings.ollama_model, local_only=self.settings.offline)
        return self._writer

    def tier_models(self) -> dict[str, str | None]:
        """Modelo usado em cada camada (`rapido`, `padrao`, `forte`)."""
        models: dict[str, str | None] = {}
        for tier in TIERS:
            writer = self.writer_for(tier)
            models[tier] = getattr(writer, "model", None) or (writer.name if writer else None)
        return models

    def writer_for(self, tier: Tier | None) -> Writer | None:
        """A LLM da camada pedida: o mesmo endpoint, trocando só o modelo.

        Camada sem modelo configurado, que já falhou nesta sessão, ou LLM que não é um endpoint
        compatível com OpenAI (ex.: `CallableWriter`) usa a LLM padrão.
        """
        base = self.writer
        if base is None or tier in (None, "padrao") or tier in self._broken_tiers:
            return base
        model = self.settings.ollama_model_fast if tier == "rapido" else self.settings.ollama_model_strong
        if not model or not isinstance(base, OpenAICompatibleWriter) or model == base.model:
            return base
        writer = self._tier_writers.get(tier)
        if writer is None:
            clone = copy.copy(base)
            clone.model = model
            writer = self._tier_writers[tier] = clone
        return writer

    def write(
        self,
        prompt: str,
        *,
        context: Any = None,
        system: str | None = None,
        max_tokens: int = 800,
        temperature: float = 0.3,
        tier: Tier | None = None,
    ) -> str:
        """Gera texto livre para uma pessoa ler. Nunca use isto para decidir um `if`.

        `tier` escolhe o modelo local (`rapido`, `padrao`, `forte`); se o modelo da camada
        falhar (ex.: não foi baixado), a chamada é refeita com o modelo padrão.
        """
        base = self.writer
        if base is None:
            raise NoWriterError(
                "Nenhuma LLM de texto configurada. Opções gratuitas: rode o Ollama localmente "
                "(https://ollama.com) ou defina JEV_CLAUD_LLM_BASE_URL e JEV_CLAUD_LLM_MODEL para um "
                "endpoint compatível com OpenAI."
            )
        url = getattr(base, "base_url", None)
        if self.settings.offline and isinstance(url, str) and not is_local_url(url):
            raise WriterError(f"JEV_CLAUD_OFFLINE=1 só aceita LLM local; esta aponta para {url}.")
        if context is not None:
            rendered = (
                context if isinstance(context, str) else json.dumps(context, ensure_ascii=False, indent=2)
            )
            prompt = f"{prompt}\n\nContexto:\n{rendered}"
        writer = self.writer_for(tier) or base
        try:
            return writer.write(prompt, system=system, max_tokens=max_tokens, temperature=temperature)
        except WriterError as error:
            if writer is base or tier is None:
                raise
            logger.warning(
                "Modelo %s falhou (%s); usando %s.",
                getattr(writer, "model", tier),
                error,
                getattr(base, "model", base.name),
            )
            self._broken_tiers.add(tier)
            return base.write(prompt, system=system, max_tokens=max_tokens, temperature=temperature)

    def summarize(self, text: str, *, focus: str | None = None, effort: str | None = None) -> str:
        """Resume um texto grande com a LLM local, quebrado em blocos conforme o `effort`.

        Os níveis seguem o seletor do app do Claude: `baixo`, `medio` (padrão), `alto`,
        `extra`, `max` e `ultracode`. Quanto mais alto, menores os blocos e mais forte o modelo
        (tabela em `jev_claud.effort`); o `ultracode` ainda revisa o resumo contra as fontes.
        A junção é hierárquica (nunca manda à LLM mais que um bloco por vez) e uma parte que
        falha não derruba o resumo: ela fica de fora e o resumo avisa.
        """
        plan = effort_plan(effort)
        focus = focus or DEFAULT_FOCUS
        chunks = split_text(text, plan.chunk_chars, plan.overlap_chars)
        if not chunks:
            raise ValueError("Nada para resumir: o texto está vazio.")
        if len(chunks) == 1:
            single = self.write(
                focus,
                context=chunks[0],
                system=SUMMARY_SYSTEM,
                max_tokens=plan.final_tokens,
                tier=plan.final_tier,
            )
            return self._verify(single, chunks[0], plan) if plan.verify else single
        partials: list[str] = []
        failed = 0
        last_error: WriterError | None = None
        for n, chunk in enumerate(chunks, 1):
            try:
                partials.append(
                    self.write(
                        f"Parte {n} de {len(chunks)} de um documento. Extraia o que importa para: {focus}",
                        context=chunk,
                        system=SUMMARY_SYSTEM,
                        max_tokens=plan.part_tokens,
                        tier=plan.part_tier,
                    )
                )
            except WriterError as error:
                failed += 1
                last_error = error
                logger.warning("Parte %d de %d falhou na LLM local: %s", n, len(chunks), error)
        if not partials:
            assert last_error is not None
            raise last_error
        while len(partials) > 1 and sum(len(p) for p in partials) > plan.chunk_chars:
            groups = pack(partials, plan.chunk_chars)
            if len(groups) == len(partials):
                break  # cada parcial já ocupa um bloco: juntar de novo não encolhe nada
            partials = [self._merge(group, focus, plan, final=False) for group in groups]
        summary = self._merge(partials, focus, plan, final=True)
        if plan.verify:
            summary = self._verify(summary, _join_parts(partials), plan)
        if failed:
            summary += (
                f"\n\n(Aviso: {failed} de {len(chunks)} partes falharam na LLM local "
                "e ficaram fora deste resumo.)"
            )
        return summary

    def _verify(self, summary: str, sources: str, plan: EffortPlan) -> str:
        """Passada de revisão (`ultracode`): tira do resumo o que as fontes não apoiam."""
        prompt = (
            "Revise o RESUMO comparando com as FONTES. Corrija ou remova toda afirmação que não "
            "estiver apoiada nas fontes e não acrescente nada novo. Devolva só o resumo revisado."
        )
        return self.write(
            prompt,
            context=f"RESUMO:\n{summary}\n\nFONTES:\n{sources}",
            system=SUMMARY_SYSTEM,
            max_tokens=plan.final_tokens,
            tier=plan.final_tier,
        )

    def _merge(self, parts: list[str], focus: str, plan: EffortPlan, *, final: bool) -> str:
        joined = _join_parts(parts)
        if final:
            prompt = f"Junte estes resumos parciais em um resumo final. {focus}"
            return self.write(
                prompt,
                context=joined,
                system=SUMMARY_SYSTEM,
                max_tokens=plan.final_tokens,
                tier=plan.final_tier,
            )
        prompt = f"Junte estes resumos parciais em um só, sem perder fatos. Foco: {focus}"
        return self.write(
            prompt,
            context=joined,
            system=SUMMARY_SYSTEM,
            max_tokens=plan.part_tokens * 2,
            tier=plan.part_tier,
        )

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
        """Retrato da configuração atual (usado por `jev-claud doctor`)."""
        spent_usd, spent_tokens, calls = self.store.spent()
        writer = self._writer if self._writer != "auto" else None
        return {
            "offline": self.settings.offline,
            "jev": {
                "sdk_installed": JevBackend.sdk_available(),
                "sdk_version": JevBackend.sdk_version(),
                "api_key": JevBackend.key_configured(self.settings.typesafe_api_key),
                "enabled": self.jev is not None and not self.budget.zero_cost,
                "model_spec": self.model_spec,
                "strategy": self.strategy,
                "cascade_accept": self.cascade_accept,
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
            "routing": {
                layer: {"decisions": n, "saved_tokens": saved}
                for layer, (n, saved) in self.store.routing().items()
            },
        }

    def close(self) -> None:
        if self.jev is not None:
            self.jev.close()
        self.store.close()

    def __enter__(self) -> JevClaud:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _triage(
        self, state: Any, questions: Mapping[str, Question], policy: Policy
    ) -> tuple[RawResult | None, list[str]]:
        """Cascata: responde offline o que der e devolve quais perguntas ficam resolvidas.

        Imagens e `Box` vão sempre ao Jev: o offline não enxerga imagens.
        """
        textual = {name: q for name, q in questions.items() if q.kind != "box"}
        if not textual or media.has_images(state, to_wire(questions)):
            return None, []
        raw = self.offline.evaluate(state, textual, model=None)
        built = _build(raw, textual, policy, source="offline")
        accepted = [name for name, d in built.items() if d.confidence >= self.cascade_accept]
        return raw, accepted

    def _offline(
        self,
        state: Any,
        questions: Mapping[str, Question],
        policy: Policy,
        *,
        raw: RawResult | None = None,
        accepted: Sequence[str] = (),
    ) -> Decisions:
        boxes = [name for name, q in questions.items() if q.kind == "box"]
        if boxes:
            raise BackendError(
                f"{', '.join(boxes)}: Box exige o Jev (modelo de visão). Configure a chave e o "
                "orçamento com `jev-claud setup`."
            )
        if media.has_images(state, to_wire(questions)):
            self._warn_once("images", "Imagens exigem o Jev: a resposta offline ignora o conteúdo da imagem.")
        if raw is None or len(accepted) < len(questions):
            raw = self.offline.evaluate(state, questions, model=None)
        if self.jev is None:
            self.store.record_routing("offline", len(questions))
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


def _join_parts(parts: Sequence[str]) -> str:
    return "\n\n".join(f"[Parte {n}]\n{part}" for n, part in enumerate(parts, 1))


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
    sources: Mapping[str, Source] | None = None,
    escalated: tuple[str, ...] = (),
) -> Decisions:
    decisions: dict[str, Decision] = {}
    default_source: Source = "jev" if source == "mixed" else source
    for name, question in questions.items():
        answer_source = (sources or {}).get(name, default_source)
        answer = raw.answers.get(name)
        if answer is None:
            raise BackendError(f"O backend não respondeu a pergunta {name!r}.")
        if answer.kind != question.kind:
            raise BackendError(
                f"Pergunta {name!r} é {question.kind}, mas a resposta veio como {answer.kind}."
            )
        if answer.kind == "noul":
            p = _number(answer.value)
            confidence = noul_confidence(p)
            decisions[name] = Decision(
                name=name,
                kind="noul",
                value=p > 0.5,
                confidence=confidence,
                band=policy.band(confidence),
                probabilities={"true": p, "false": 1.0 - p},
                source=answer_source,
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
                source=answer_source,
                model=raw.model,
                probability=probabilities.get(label),
            )
        elif answer.kind == "box":
            if not isinstance(answer.value, Mapping):
                raise BackendError(f"Resposta Box inválida para {name!r}: {answer.value!r}")
            region = Region(
                **{side: float(answer.value[side]) for side in ("top", "left", "bottom", "right")}
            )
            # Sem confiança informada pelo modelo: faixa MEDIUM (confirmar antes de agir).
            known = answer.confidence is not None
            confidence = float(answer.confidence) if answer.confidence is not None else policy.confirm
            decisions[name] = Decision(
                name=name,
                kind="box",
                value=region,
                confidence=confidence,
                band=policy.band(confidence) if known else Band.MEDIUM,
                probabilities={},
                source=answer_source,
                model=raw.model,
            )
        else:
            levels = {int(k): float(v) for k, v in answer.probabilities.items()}
            confidence = answer.confidence if answer.confidence is not None else score_confidence(levels)
            mode = max(levels, key=lambda level: levels[level]) if levels else None
            decisions[name] = Decision(
                name=name,
                kind="score",
                value=_number(answer.value),
                confidence=confidence,
                band=policy.band(confidence),
                probabilities=levels,
                source=answer_source,
                model=raw.model,
                probability=levels.get(mode) if mode is not None else None,
                level=mode,
                legend={int(k): v for k, v in answer.legend.items()},
            )
    return Decisions(
        items_=decisions,
        source=source,
        model=raw.model,
        input_tokens=raw.input_tokens,
        cost_usd=cost_usd,
        escalated=escalated,
    )


def _number(value: str | float | Mapping[str, float]) -> float:
    if isinstance(value, Mapping):
        raise BackendError(f"Resposta numérica esperada, veio {value!r}.")
    return float(value)


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
