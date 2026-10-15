from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from typing import Any

import pytest

from conftest import FakeClient, choice_answer, noul_answer, score_answer
from jev_claud import (
    BackendError,
    Band,
    BudgetExceeded,
    CallableWriter,
    Case,
    JevBackend,
    JevClaud,
    NoWriterError,
    Settings,
    choice,
    noul,
    score,
)

Factory = Callable[..., JevClaud]

QUESTIONS = {
    "setor": choice("Qual setor?", {"financeiro": "cobrança", "suporte": "erro"}),
    "urgente": noul("É urgente?"),
    "raiva": score("Quão irritado?", ["calmo", "frustrado", "furioso"]),
}


def ticket_answers() -> dict[str, Any]:
    return {
        "setor": choice_answer({"financeiro": 0.9, "suporte": 0.1}),
        "urgente": noul_answer(0.95),
        "raiva": score_answer([0.1, 0.7, 0.2], ["calmo", "frustrado", "furioso"], confidence=0.6),
    }


# -- seleção de backend -------------------------------------------------------


def test_without_key_everything_runs_offline_for_free() -> None:
    with JevClaud(store=":memory:", writer=None) as r:
        assert r.jev is None
        d = r.decide("Fui cobrado duas vezes", QUESTIONS)
    assert d.source == "offline"
    assert d["setor"].value == "financeiro"
    assert d.cost_usd == 0.0


def test_auto_enables_jev_when_key_and_sdk_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-teste")
    with JevClaud(store=":memory:", writer=None) as r:
        assert isinstance(r.jev, JevBackend)


# -- Jev, cache e orçamento ---------------------------------------------------


def test_jev_answers_become_typed_decisions(make_jev: Factory) -> None:
    client = FakeClient(ticket_answers())
    r = make_jev(client)
    d = r.decide("Fui cobrado duas vezes!", QUESTIONS)

    assert d.source == "jev" and d.model == "jev-1.13.0"
    assert d["setor"].value == "financeiro" and d["setor"].is_("financeiro")
    assert d["setor"].probability == pytest.approx(0.9)
    assert d["setor"].band is Band.HIGH
    assert d["urgente"].yes and d["urgente"].probability == pytest.approx(0.95)
    assert d["urgente"].confidence == pytest.approx(0.9)
    assert d["raiva"].value == pytest.approx(1.1)
    assert d["raiva"].level == 1 and d["raiva"].legend[1] == "frustrado"
    assert d["raiva"].band is Band.MEDIUM
    assert d.input_tokens == 300
    assert d.cost_usd == pytest.approx(300 * 0.042 / 1_000_000)

    sent = client.calls[0]
    assert sent["model"] == "jev-latest"
    assert sent["questions"]["setor"] == {
        "type": "choice",
        "instructions": "Qual setor?",
        "criteria": {"financeiro": "cobrança", "suporte": "erro"},
    }


def test_repeated_decision_comes_from_cache_at_zero_cost(make_jev: Factory) -> None:
    client = FakeClient(ticket_answers())
    r = make_jev(client)
    first = r.decide("mesmo texto", QUESTIONS)
    second = r.decide("mesmo texto", QUESTIONS)

    assert len(client.calls) == 1
    assert second.cached and second.cost_usd == 0.0
    assert second["setor"].value == first["setor"].value
    assert second["raiva"].legend == first["raiva"].legend
    assert r.store.spent()[2] == 1  # só uma chamada cobrada


def test_cache_can_be_bypassed_and_is_keyed_by_model(make_jev: Factory) -> None:
    client = FakeClient(ticket_answers())
    r = make_jev(client)
    r.decide("texto", QUESTIONS)
    r.decide("texto", QUESTIONS, cache=False)
    r.decide("texto", QUESTIONS, model="preview")
    assert [c["model"] for c in client.calls] == ["jev-latest", "jev-latest", "jev-preview"]


def test_cache_ttl_zero_disables_cache(make_jev: Factory) -> None:
    client = FakeClient(ticket_answers())
    r = make_jev(client, settings=Settings(cache_ttl_days=0))
    r.decide("texto", QUESTIONS)
    r.decide("texto", QUESTIONS)
    assert len(client.calls) == 2
    assert r.store.cache_size() == 0


def test_pinned_model_cache_never_expires(make_jev: Factory) -> None:
    r = make_jev(FakeClient())
    assert r._cache_ttl("jev-1.13.0") is None
    assert r._cache_ttl("jev-latest") == pytest.approx(7 * 24 * 3600)


def test_zero_budget_never_calls_jev_and_warns_once(
    make_jev: Factory, caplog: pytest.LogCaptureFixture
) -> None:
    client = FakeClient(ticket_answers())
    r = make_jev(client, budget=0.0)
    with caplog.at_level(logging.WARNING, logger="jev_claud"):
        d1 = r.decide("Fui cobrado duas vezes", QUESTIONS)
        d2 = r.decide("Outro texto", QUESTIONS)
    assert client.calls == []
    assert d1.source == d2.source == "offline"
    assert sum("JEV_CLAUD_BUDGET_USD" in m for m in caplog.messages) == 1


def test_budget_is_enforced_across_calls(make_jev: Factory) -> None:
    client = FakeClient(ticket_answers(), input_tokens=2_000_000)  # US$ 0,084 por chamada
    r = make_jev(client, budget=0.05)
    assert r.decide("a", QUESTIONS).source == "jev"  # a estimativa prévia cabia no teto
    assert r.decide("b", QUESTIONS).source == "offline"  # o gasto real já estourou
    assert len(client.calls) == 1
    assert r.store.spent()[0] == pytest.approx(0.084)


def test_incomplete_answers_are_not_cached(make_jev: Factory) -> None:
    client = FakeClient({"setor": choice_answer({"financeiro": 0.9, "suporte": 0.1})})
    r = make_jev(client)
    for _ in range(2):
        with pytest.raises(BackendError):
            r.decide("texto", QUESTIONS)
    assert len(client.calls) == 2
    assert r.store.cache_size() == 0


def test_budget_raise_mode(make_jev: Factory) -> None:
    r = make_jev(FakeClient(ticket_answers()), budget=0.0, fallback="raise")
    with pytest.raises(BudgetExceeded):
        r.decide("texto", QUESTIONS)


def test_backend_failure_falls_back_to_offline(make_jev: Factory) -> None:
    r = make_jev(FakeClient(error=RuntimeError("503")))
    d = r.decide("Fui cobrado duas vezes", QUESTIONS)
    assert d.source == "offline"
    assert r.store.spent() == (0.0, 0, 0)


def test_backend_failure_raise_mode(make_jev: Factory) -> None:
    r = make_jev(FakeClient(error=RuntimeError("503")), fallback="raise")
    with pytest.raises(BackendError, match="503"):
        r.decide("texto", QUESTIONS)


def test_missing_or_mismatched_answers_are_errors(make_jev: Factory) -> None:
    r = make_jev(FakeClient({"setor": noul_answer(0.5)}))
    with pytest.raises(BackendError):
        r.decide("texto", {"setor": QUESTIONS["setor"]})
    with pytest.raises(BackendError):
        r.decide("texto", QUESTIONS)


def test_unknown_token_usage_is_estimated(make_jev: Factory) -> None:
    r = make_jev(FakeClient(ticket_answers(), input_tokens=None))
    d = r.decide("texto", QUESTIONS)
    assert d.cost_usd > 0
    assert r.store.spent()[1] > 300


# -- ramificação ---------------------------------------------------------------


def _noul_client(p: float) -> FakeClient:
    return FakeClient(lambda questions: {name: noul_answer(p) for name in questions})


def _choice_client(probabilities: Mapping[str, float]) -> FakeClient:
    return FakeClient(lambda questions: {name: choice_answer(probabilities) for name in questions})


def test_when_branches_on_condition(make_jev: Factory) -> None:
    assert make_jev(_noul_client(0.97)).when("x", "Quer cancelar?", then="sim", otherwise="não") == "sim"
    assert make_jev(_noul_client(0.03)).when("x", "Quer cancelar?", then="sim", otherwise="não") == "não"


def test_when_uses_uncertain_branch_and_calls_handlers(make_jev: Factory) -> None:
    r = make_jev(_noul_client(0.55))
    seen: list[float | None] = []
    result = r.when(
        "x",
        noul("Quer cancelar?"),
        then=lambda: "then",
        otherwise=lambda: "otherwise",
        uncertain=lambda d: seen.append(d.probability) or "perguntar",
    )
    assert result == "perguntar"
    assert seen == [pytest.approx(0.55)]


def test_switch_runs_chosen_case(make_jev: Factory) -> None:
    r = make_jev(_choice_client({"pedido": 0.9, "reclamacao": 0.05, "duvida": 0.05}))
    result = r.switch(
        "Cadê meu pedido?",
        "Qual a intenção?",
        {
            "pedido": Case("rastreio, entrega", then=lambda d: f"consultar ({d.confidence:.2f})"),
            "reclamacao": Case("defeito", then="abrir reclamação"),
            "duvida": "responder",
        },
        default="humano",
    )
    assert result.startswith("consultar")
    sent = r.jev.client.calls[0]["questions"]["answer"]  # type: ignore[union-attr]
    assert sent["criteria"] == {"pedido": "rastreio, entrega", "reclamacao": "defeito", "duvida": None}


def test_switch_low_confidence_goes_to_default(make_jev: Factory) -> None:
    r = make_jev(_choice_client({"a": 0.4, "b": 0.35, "c": 0.25}))
    assert r.switch("x", "?", {"a": 1, "b": 2, "c": 3}, default="humano") == "humano"
    assert r.switch("x", "?", {"a": 1, "b": 2, "c": 3}) == 1  # sem default, arrisca o melhor palpite


def test_switch_case_without_then_returns_label(make_jev: Factory) -> None:
    r = make_jev(_choice_client({"a": 0.95, "b": 0.05}))
    assert r.switch("x", "?", {"a": Case("primeiro"), "b": Case("segundo")}) == "a"


def test_rank_orders_by_score(make_jev: Factory) -> None:
    by_item = {"c0": [0.9, 0.1, 0.0, 0.0], "c1": [0.0, 0.0, 0.1, 0.9], "c2": [0.0, 0.8, 0.2, 0.0]}
    levels = ["0", "1", "2", "3"]
    r = make_jev(FakeClient(lambda qs: {n: score_answer(by_item[n], levels) for n in qs}))
    ranked = r.rank("cancelar", ["bolo", "cancelamento", "planos"])
    assert [hit.item for hit in ranked] == ["cancelamento", "planos", "bolo"]
    assert r.rank("cancelar", ["bolo", "cancelamento", "planos"], top_k=1)[0].index == 1
    assert len(r.jev.client.calls) == 1  # type: ignore[union-attr]  # segunda veio do cache
    assert r.rank("x", []) == []


def test_rank_offline() -> None:
    with JevClaud(store=":memory:", writer=None) as r:
        ranked = r.rank("cancelar assinatura", ["receita de bolo", "como cancelar a assinatura"])
    assert ranked[0].item == "como cancelar a assinatura"


# -- texto ---------------------------------------------------------------------


def test_write_requires_a_writer() -> None:
    with JevClaud(store=":memory:", writer=None) as r, pytest.raises(NoWriterError):
        r.write("oi")


def test_write_auto_detects_nothing_in_clean_env() -> None:
    with JevClaud(store=":memory:") as r:
        assert r.writer is None


def test_write_passes_context_and_system() -> None:
    received: list[tuple[str, str | None]] = []

    def fake_llm(prompt: str, system: str | None) -> str:
        received.append((prompt, system))
        return "Olá!"

    with JevClaud(store=":memory:", writer=CallableWriter(fake_llm)) as r:
        assert r.write("Responda ao cliente.", context={"ticket": "cobrança"}, system="Seja breve.") == "Olá!"
    prompt, system = received[0]
    assert prompt.startswith("Responda ao cliente.") and '"ticket": "cobrança"' in prompt
    assert system == "Seja breve."


def test_async_api(make_jev: Factory) -> None:
    r = make_jev(FakeClient({**ticket_answers(), "answer": noul_answer(0.9)}))
    r._writer = CallableWriter(lambda prompt, system: prompt.upper())

    async def run() -> tuple[Any, Any, str]:
        return (
            await r.adecide("texto", QUESTIONS),
            await r.aask("texto", QUESTIONS["urgente"]),
            await r.awrite("oi"),
        )

    decisions, single, text = asyncio.run(run())
    assert decisions["setor"].value == "financeiro"
    assert single.kind == "noul"
    assert text == "OI"


# -- modelos e status ------------------------------------------------------------


def test_model_resolution(make_jev: Factory) -> None:
    client = FakeClient(
        models=[
            ("jev-latest", "2026-09-01"),
            ("jev-1.13.0", "2026-09-01"),
            ("jev-1.14.0", "2026-10-14"),
            ("jev-preview", "2026-10-14"),
        ]
    )
    r = make_jev(client)
    assert r.resolve_model() == "jev-latest"
    assert r.resolve_model("stable") == "jev-latest"
    assert r.resolve_model("preview") == "jev-preview"
    assert r.resolve_model("jev-1.13.0") == "jev-1.13.0"
    assert r.resolve_model("newest") == "jev-1.14.0"
    client.models.list = lambda: (_ for _ in ()).throw(AssertionError("deveria usar o cache"))
    assert r.resolve_model("newest") == "jev-1.14.0"


def test_newest_falls_back_to_preview_on_error(make_jev: Factory) -> None:
    client = FakeClient()

    def boom() -> None:
        raise RuntimeError("fora do ar")

    client.models.list = boom
    assert make_jev(client).resolve_model("newest") == "jev-preview"


def test_model_from_settings(make_jev: Factory) -> None:
    client = FakeClient(ticket_answers())
    r = make_jev(client, settings=Settings(model="jev-1.13.0"))
    r.decide("texto", QUESTIONS)
    assert client.calls[0]["model"] == "jev-1.13.0"


def test_status_reports_configuration(make_jev: Factory) -> None:
    r = make_jev(FakeClient(ticket_answers()), budget=2.0)
    r.decide("texto", QUESTIONS)
    status = r.status()
    assert status["jev"]["enabled"] is True
    assert status["budget"]["monthly_usd"] == 2.0
    assert status["budget"]["calls_this_month"] == 1
    assert status["cache"]["entries"] == 1


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_CLAUD_BUDGET_USD", "1,50")
    monkeypatch.setenv("JEV_CLAUD_MODEL", "preview")
    settings = Settings.from_env()
    assert settings.budget_usd == 1.5 and settings.model == "preview"
    monkeypatch.setenv("JEV_CLAUD_BUDGET_USD", "muito")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_close_closes_client(make_jev: Factory) -> None:
    client = FakeClient()
    make_jev(client).close()
    assert client.closed
