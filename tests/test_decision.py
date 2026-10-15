import pytest

from ramifica import Band, Decision, Policy
from ramifica.decision import choice_confidence, noul_confidence, score_confidence


def test_choice_confidence_matches_docs_formula() -> None:
    assert choice_confidence({"a": 1 / 3, "b": 1 / 3, "c": 1 / 3}) == pytest.approx(0.0)
    assert choice_confidence({"a": 1.0, "b": 0.0}) == pytest.approx(1.0)
    assert choice_confidence({"a": 0.8, "b": 0.1, "c": 0.1}) == pytest.approx(0.7)
    assert choice_confidence({"único": 1.0}) == 1.0


def test_score_confidence_rewards_concentration_and_near_neighbours() -> None:
    assert score_confidence({0: 0.0, 1: 1.0, 2: 0.0}) == pytest.approx(1.0)
    assert score_confidence({0: 1 / 3, 1: 1 / 3, 2: 1 / 3}) == pytest.approx(0.0)
    near = score_confidence({0: 0.0, 1: 0.7, 2: 0.3, 3: 0.0})
    far = score_confidence({0: 0.3, 1: 0.7, 2: 0.0, 3: 0.0})
    spread = score_confidence({0: 0.0, 1: 0.7, 2: 0.0, 3: 0.3})
    assert near == pytest.approx(far)
    assert near > spread


def test_noul_confidence() -> None:
    assert noul_confidence(0.5) == 0.0
    assert noul_confidence(0.9) == pytest.approx(0.8)
    assert noul_confidence(0.0) == 1.0


def test_policy_bands() -> None:
    policy = Policy(act=0.8, confirm=0.4)
    assert policy.band(0.8) is Band.HIGH
    assert policy.band(0.5) is Band.MEDIUM
    assert policy.band(0.39) is Band.LOW


def test_policy_validates_thresholds() -> None:
    with pytest.raises(ValueError):
        Policy(act=0.3, confirm=0.6)


def _decision(kind: str, value: object) -> Decision:
    return Decision(
        name="q",
        kind=kind,
        value=value,
        confidence=0.9,
        band=Band.HIGH,
        probabilities={},
        source="offline",  # type: ignore[arg-type]
    )


def test_decision_helpers_check_kind() -> None:
    assert _decision("noul", True).yes
    assert _decision("choice", "a").is_("a")
    assert _decision("score", 1.6).at_least(1.5)
    with pytest.raises(TypeError):
        _ = _decision("choice", "a").yes
    with pytest.raises(TypeError):
        _decision("noul", True).is_("a")
    with pytest.raises(TypeError):
        _decision("choice", "a").at_least(1)
