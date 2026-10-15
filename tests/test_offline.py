import pytest

from jev_claude import OfflineBackend, choice, noul, score
from jev_claude.backends.offline import features, resolve_path


@pytest.fixture
def offline() -> OfflineBackend:
    return OfflineBackend()


def test_features_ignore_accents_case_and_stopwords() -> None:
    assert features("COBRANÇA da Fatura") == features("cobranca fatura")
    assert "da" not in features("cobrança da fatura")


def test_choice_picks_option_whose_terms_appear(offline: OfflineBackend) -> None:
    result = offline.evaluate(
        "Fui cobrado duas vezes na fatura deste mês",
        {"setor": choice("Qual setor?", {"financeiro": "cobrança, fatura", "suporte": "erro, bug"})},
    )
    answer = result.answers["setor"]
    assert answer.value == "financeiro"
    assert answer.confidence is not None and answer.confidence > 0.5
    assert sum(answer.probabilities.values()) == pytest.approx(1.0)
    assert result.input_tokens == 0


def test_choice_without_evidence_is_uniform_and_unconfident(offline: OfflineBackend) -> None:
    answer = offline.evaluate("olá", {"x": choice("?", ["alfa", "beta", "gama"])}).answers["x"]
    assert all(p == pytest.approx(1 / 3) for p in answer.probabilities.values())
    assert answer.confidence == pytest.approx(0.0)


def test_noul_with_described_outcomes(offline: OfflineBackend) -> None:
    q = noul("É urgente?", yes="urgente, imediato, hoje", no="sem pressa, quando puder")
    assert float(offline.evaluate("Preciso disso hoje, é urgente", {"u": q}).answers["u"].value) > 0.8
    assert float(offline.evaluate("Sem pressa, responda quando puder", {"u": q}).answers["u"].value) < 0.2


def test_noul_without_evidence_means_no() -> None:
    from jev_claude import JevClaude

    with JevClaude(store=":memory:", writer=None) as r:
        decision = r.ask("bom dia", noul("Fala de reembolso?"))
    assert decision.yes is False
    assert decision.uncertain


def test_noul_without_evidence_is_half(offline: OfflineBackend) -> None:
    assert offline.evaluate("bom dia", {"u": noul("Fala de reembolso?")}).answers["u"].value == pytest.approx(
        0.5
    )


def test_score_from_level_terms(offline: OfflineBackend) -> None:
    q = score("Quão irritado?", ["calmo, tranquilo", "frustrado, chateado", "furioso, absurdo, revoltado"])
    answer = offline.evaluate("Isso é um absurdo, estou furioso!", {"s": q}).answers["s"]
    assert float(answer.value) > 1.5
    assert answer.legend["2"].startswith("furioso")


def test_score_with_focus_path_measures_relevance_to_context(offline: OfflineBackend) -> None:
    state = {
        "query": "cancelar assinatura",
        "candidates": ["cancelar a assinatura mensal", "receita de bolo"],
    }
    levels = ["irrelevante", "relevante"]
    relevant = offline.evaluate(state, {"s": score("`candidates[0]` serve para `query`?", levels)})
    unrelated = offline.evaluate(state, {"s": score("`candidates[1]` serve para `query`?", levels)})
    assert float(relevant.answers["s"].value) > float(unrelated.answers["s"].value)


def test_resolve_path() -> None:
    state = {"ticket": {"messages": [{"text": "oi"}, {"text": "tchau"}]}}
    assert resolve_path(state, "ticket.messages[1].text") == (True, "tchau")
    assert resolve_path(state, "ticket.messages[5].text") == (False, None)
    assert resolve_path(state, "ticket.nada") == (False, None)


def test_relevance_helper(offline: OfflineBackend) -> None:
    assert offline.relevance("plano anual", "Plano anual com desconto") > offline.relevance(
        "plano anual", "bolo"
    )
