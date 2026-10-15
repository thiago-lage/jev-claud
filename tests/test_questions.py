import pytest

from jev_claude import QuestionError, choice, noul, score
from jev_claude.questions import Choice, to_wire


def test_choice_from_list_has_undescribed_options() -> None:
    q = choice("Qual setor?", ["financeiro", "suporte"])
    assert q.to_wire() == {
        "type": "choice",
        "instructions": "Qual setor?",
        "criteria": {"financeiro": None, "suporte": None},
    }


def test_choice_from_mapping_keeps_descriptions() -> None:
    q = choice({"task": "classificar"}, {"a": "primeira", "b": {"exemplos": ["x"]}})
    assert q.to_wire()["criteria"] == {"a": "primeira", "b": {"exemplos": ["x"]}}
    assert q.to_wire()["instructions"] == {"task": "classificar"}


@pytest.mark.parametrize("options", [[], {}, "abc"])
def test_choice_rejects_bad_options(options: object) -> None:
    with pytest.raises(QuestionError):
        choice("x", options)  # type: ignore[arg-type]


def test_choice_rejects_too_many_options() -> None:
    with pytest.raises(QuestionError):
        Choice("x", {str(i): None for i in range(256)})


def test_noul_wire_with_and_without_criteria() -> None:
    assert noul("É urgente?").to_wire() == {"type": "noul", "instructions": "É urgente?"}
    assert noul("É urgente?", yes="pede ação hoje").to_wire() == {
        "type": "noul",
        "instructions": "É urgente?",
        "criteria": {"true": "pede ação hoje", "false": None},
    }
    assert noul(None, no="pode esperar").to_wire() == {
        "type": "noul",
        "criteria": {"true": None, "false": "pode esperar"},
    }


def test_noul_requires_something_to_ask() -> None:
    with pytest.raises(QuestionError):
        noul(None)


@pytest.mark.parametrize("levels", [["só um"], [str(i) for i in range(11)], "abc"])
def test_score_level_bounds(levels: object) -> None:
    with pytest.raises(QuestionError):
        score("x", levels)  # type: ignore[arg-type]


def test_score_wire() -> None:
    assert score("Quão bravo?", ("calmo", "bravo")).to_wire() == {
        "type": "score",
        "instructions": "Quão bravo?",
        "criteria": ["calmo", "bravo"],
    }


def test_to_wire_requires_questions() -> None:
    with pytest.raises(QuestionError):
        to_wire({})
