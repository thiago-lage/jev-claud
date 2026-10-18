"""Cascata (cache -> offline -> Jev só nos incertos) e imagens/Box do System One."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typesafe_sdk import BoxAnswer, BoxCoordinates
from typesafe_sdk import Image as SdkImage

from conftest import FakeClient, choice_answer, noul_answer, score_answer
from jev_claud import Band, Box, Budget, Image, JevBackend, JevClaud, Settings, box, choice, noul
from jev_claud.budget import REQUEST_OVERHEAD_TOKENS
from jev_claud.errors import BackendError, QuestionError
from jev_claud.media import IMAGE_TOKENS

SETOR = choice(
    "Qual setor?",
    {"financeiro": "cobrança, fatura, pagamento, reembolso", "suporte": "erro, bug, travou, não funciona"},
)
URGENTE = noul("A mensagem pede ação imediata?")
OBVIO = "Fui cobrado duas vezes na fatura, quero reembolso"  # offline acerta o setor com folga

Factory = Callable[..., JevClaud]


def answers_for(**by_name: Any) -> Callable[[Any], dict[str, Any]]:
    return lambda questions: {name: by_name[name] for name in questions}


def test_obvious_answers_stay_offline_and_only_uncertain_go_to_jev(make_jev: Factory) -> None:
    client = FakeClient(
        answers_for(urgente=noul_answer(0.9), setor=choice_answer({"financeiro": 0.1, "suporte": 0.9}))
    )
    r = make_jev(client, strategy="cascade")
    d = r.decide(OBVIO, {"setor": SETOR, "urgente": URGENTE})

    assert len(client.calls) == 1
    assert list(client.calls[0]["questions"]) == ["urgente"]  # só a incerta foi paga
    assert d.source == "mixed" and d.escalated == ("urgente",)
    assert d["setor"].value == "financeiro" and d["setor"].source == "offline"
    assert d["urgente"].yes and d["urgente"].source == "jev"
    assert d.cost_usd > 0
    routing = r.store.routing()
    assert routing["offline"][0] == 1 and routing["offline"][1] > 0  # tokens economizados
    assert routing["jev"] == (1, 0)


def test_everything_obvious_never_calls_jev(make_jev: Factory) -> None:
    client = FakeClient()
    r = make_jev(client, strategy="cascade")
    d = r.decide(OBVIO, {"setor": SETOR})
    assert client.calls == []
    assert d.source == "offline" and d.cost_usd == 0 and d.escalated == ()
    assert r.store.spent() == (0.0, 0, 0)


def test_negation_escalates_instead_of_accepting_lexical_confidence(make_jev: Factory) -> None:
    client = FakeClient(answers_for(setor=choice_answer({"financeiro": 0.01, "suporte": 0.99})))
    r = make_jev(client, strategy="cascade")
    d = r.decide("Não é cobrança, nem fatura, nem pagamento, nem reembolso. É um bug.", {"setor": SETOR})
    assert len(client.calls) == 1 and d["setor"].source == "jev" and d["setor"].value == "suporte"


def test_direct_strategy_does_not_reuse_mixed_cache(make_jev: Factory) -> None:
    client = FakeClient(
        answers_for(urgente=noul_answer(0.9), setor=choice_answer({"financeiro": 0.01, "suporte": 0.99}))
    )
    r = make_jev(client, strategy="cascade")
    questions = {"setor": SETOR, "urgente": URGENTE}
    assert r.decide(OBVIO, questions).source == "mixed"
    direct = r.decide(OBVIO, questions, strategy="jev")
    assert len(client.calls) == 2 and direct["setor"].value == "suporte"


def test_nothing_obvious_goes_entirely_to_jev(make_jev: Factory) -> None:
    client = FakeClient(
        answers_for(setor=choice_answer({"financeiro": 0.8, "suporte": 0.2}), urgente=noul_answer(0.1))
    )
    r = make_jev(client, strategy="cascade")
    d = r.decide("Olá, tudo bem?", {"setor": SETOR, "urgente": URGENTE})
    assert list(client.calls[0]["questions"]) == ["setor", "urgente"]
    assert d.source == "jev" and d.escalated == ()
    assert {x.source for x in d.values()} == {"jev"}


def test_accept_threshold_one_sends_everything(make_jev: Factory) -> None:
    client = FakeClient(answers_for(setor=choice_answer({"financeiro": 0.9, "suporte": 0.1})))
    r = make_jev(client, strategy="cascade", cascade_accept=1.0)
    r.decide(OBVIO, {"setor": SETOR})
    assert len(client.calls) == 1


def test_strategy_jev_skips_offline(make_jev: Factory) -> None:
    client = FakeClient(answers_for(setor=choice_answer({"financeiro": 0.9, "suporte": 0.1})))
    r = make_jev(client, strategy="cascade")
    d = r.decide(OBVIO, {"setor": SETOR}, strategy="jev")
    assert len(client.calls) == 1 and d.source == "jev"


def test_escalate_all_keeps_answers_comparable(make_jev: Factory) -> None:
    client = FakeClient(
        answers_for(setor=choice_answer({"financeiro": 0.9, "suporte": 0.1}), urgente=noul_answer(0.2))
    )
    r = make_jev(client, strategy="cascade")
    d = r.decide(OBVIO, {"setor": SETOR, "urgente": URGENTE}, escalate="all")
    assert list(client.calls[0]["questions"]) == ["setor", "urgente"]
    assert d.source == "jev"


def test_rank_never_mixes_offline_and_jev_scores(make_jev: Factory) -> None:
    levels = ["0", "1", "2", "3"]
    client = FakeClient(lambda qs: {n: score_answer([0.25, 0.25, 0.25, 0.25], levels) for n in qs})
    r = make_jev(client, strategy="cascade")
    ranked = r.rank("cancelar assinatura", ["cancelar assinatura agora", "receita de bolo"], levels=levels)
    sources = {hit.decision.source for hit in ranked}
    assert len(sources) == 1


def test_mixed_result_is_cached_whole(make_jev: Factory) -> None:
    client = FakeClient(answers_for(urgente=noul_answer(0.9)))
    r = make_jev(client, strategy="cascade")
    questions = {"setor": SETOR, "urgente": URGENTE}
    r.decide(OBVIO, questions)
    again = r.decide(OBVIO, questions)
    assert len(client.calls) == 1
    assert again.source == "cache"
    assert again["setor"].value == "financeiro" and again["urgente"].yes
    assert r.store.routing()["cache"][0] == 2


def test_budget_exhausted_falls_back_to_offline(make_jev: Factory, caplog: pytest.LogCaptureFixture) -> None:
    client = FakeClient()
    r = make_jev(client, strategy="cascade", budget=0.0)
    with caplog.at_level(logging.WARNING, logger="jev_claud"):
        d = r.decide(OBVIO, {"setor": SETOR, "urgente": URGENTE})
    assert client.calls == []
    assert d.source == "offline" and d["setor"].value == "financeiro"
    assert r.store.routing()["fallback"] == (1, 0)
    assert "offline" in caplog.text


def test_jev_down_falls_back_or_raises(make_jev: Factory) -> None:
    client = FakeClient(error=RuntimeError("fora do ar"))
    assert make_jev(client, strategy="cascade").decide("Olá", {"urgente": URGENTE}).source == "offline"
    with pytest.raises(BackendError):
        make_jev(client, strategy="cascade", fallback="raise").decide("Olá", {"urgente": URGENTE})


def test_strategy_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_CLAUD_STRATEGY", "JEV")
    monkeypatch.setenv("JEV_CLAUD_CASCADE_ACCEPT", "0,9")
    settings = Settings.from_env()
    assert settings.strategy == "jev" and settings.cascade_accept == 0.9
    assert Settings().strategy == "cascade"
    monkeypatch.setenv("JEV_CLAUD_STRATEGY", "tudo")
    with pytest.raises(ValueError, match="cascade ou jev"):
        Settings.from_env()
    with pytest.raises(ValueError):
        JevClaud(jev=None, writer=None, store=":memory:", strategy="x")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        JevClaud(jev=None, writer=None, store=":memory:", cascade_accept=1.5)


# ---------------------------------------------------------------------------
# Imagens e Box
# ---------------------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_image_reference_is_stable_and_typed(tmp_path: Path) -> None:
    file = tmp_path / "foto.png"
    file.write_bytes(PNG)
    a, b = Image(file), Image(PNG)
    assert str(a) == str(b) and a == b and hash(a) == hash(b)
    assert a.reference.startswith("<image:") and len(a.reference) == len("<image:>") + 32
    assert a.content_type == "image/png" and a.path == file
    with pytest.raises(QuestionError):
        Image(tmp_path / "nao-existe.png")
    with pytest.raises(QuestionError):
        Image(b"")


def test_images_always_go_to_jev_with_sdk_assets(make_jev: Factory) -> None:
    foto = Image(PNG)
    client = FakeClient(answers_for(danificado=noul_answer(0.95)))
    r = make_jev(client, strategy="cascade")
    d = r.decide({"foto": foto, "pedido": "#123"}, {"danificado": noul("O produto está danificado?")})

    call = client.calls[0]
    assert call["state"] == {"foto": foto.reference, "pedido": "#123"}
    assert list(call["assets"]) == [foto.reference]
    sdk_image = call["assets"][foto.reference]
    assert isinstance(sdk_image, SdkImage)
    assert d["danificado"].yes and d.source == "jev"


def test_image_inside_text_and_instructions(make_jev: Factory) -> None:
    antes, depois = Image(PNG), Image(PNG + b"\x01")
    client = FakeClient(answers_for(mudou=noul_answer(0.8)))
    r = make_jev(client, strategy="cascade")
    r.decide(f"Antes: {antes}", {"mudou": noul(f"Comparada a {depois}, a peça mudou?")})
    assert set(client.calls[0]["assets"]) == {antes.reference, depois.reference}


def test_unknown_image_reference_is_rejected(make_jev: Factory) -> None:
    r = make_jev(FakeClient(answers_for(x=noul_answer(0.5))), strategy="jev", fallback="raise")
    with pytest.raises(QuestionError, match="sem a Image"):
        r.decide("Veja <image:" + "f" * 32 + ">", {"x": noul("Algo?")})


def test_budget_counts_image_tokens() -> None:
    foto = Image(PNG)
    budget = Budget(1.0)
    with_image = budget.estimate_tokens({"foto": foto}, {"x": {"type": "noul"}})
    without = budget.estimate_tokens({"foto": "texto"}, {"x": {"type": "noul"}})
    assert with_image - without >= IMAGE_TOKENS - 20
    assert with_image > REQUEST_OVERHEAD_TOKENS


@pytest.mark.parametrize(("confidence", "band"), [(None, Band.MEDIUM), (0.92, Band.HIGH), (0.2, Band.LOW)])
def test_box_answers_become_regions(make_jev: Factory, confidence: float | None, band: Band) -> None:
    coords = BoxCoordinates(top=0.2, left=0.1, bottom=0.6, right=0.5)
    client = FakeClient(answers_for(dano=BoxAnswer(box=coords, confidence=confidence)))
    r = make_jev(client, strategy="cascade")
    d = r.decide(Image(PNG), {"dano": box("Onde está o amassado?")})["dano"]
    assert d.kind == "box" and d.band is band
    region = d.region
    assert (region.top, region.left, region.bottom, region.right) == (0.2, 0.1, 0.6, 0.5)
    assert region.width == pytest.approx(0.4) and region.area == pytest.approx(0.16)
    assert region.center == pytest.approx((0.3, 0.4))
    assert region.to_pixels(1000, 500) == (100, 100, 500, 300)
    assert d.as_dict()["value"] == {"top": 0.2, "left": 0.1, "bottom": 0.6, "right": 0.5}
    with pytest.raises(TypeError):
        _ = d.number
    with pytest.raises(TypeError):
        r.decide(OBVIO, {"setor": SETOR})["setor"].region  # noqa: B018


def test_box_needs_jev() -> None:
    with (
        JevClaud(jev=None, writer=None, store=":memory:") as r,
        pytest.raises(BackendError, match="Box exige"),
    ):
        r.decide(Image(PNG), {"dano": Box("Onde está o dano?")})
    with pytest.raises(QuestionError):
        Box("")


def test_image_offline_warns_once(caplog: pytest.LogCaptureFixture) -> None:
    with (
        JevClaud(jev=None, writer=None, store=":memory:") as r,
        caplog.at_level(logging.WARNING, "jev_claud"),
    ):
        r.decide(Image(PNG), {"x": noul("Está danificado?")})
        r.decide(Image(PNG), {"x": noul("Está danificado?")})
    assert caplog.text.count("Imagens exigem o Jev") == 1


def test_temporary_image_survives_until_sent() -> None:
    """Regressão: uma Image criada dentro do estado não pode ser coletada antes do envio."""
    client = FakeClient(answers_for(x=noul_answer(0.7)))
    JevBackend(client=client).evaluate({"foto": Image(PNG + b"\x02")}, {"x": noul("Algo?")}, model="m")
    assert len(client.calls[0]["assets"]) == 1


# ---------------------------------------------------------------------------
# CLI e MCP
# ---------------------------------------------------------------------------


def test_cli_ask_with_image_and_box_needs_jev(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from jev_claud.cli import main

    foto = tmp_path / "foto.png"
    foto.write_bytes(PNG)
    assert main(["ask", "--image", str(foto), "--box", "Onde está o dano?"]) == 1
    assert "Box exige o Jev" in capsys.readouterr().err
    assert main(["ask", "--noul", "Algo?"]) == 1
    assert main(["ask", "pedido #1", "--image", str(foto), "--noul", "Está danificado?"]) == 0
    assert "via offline" in capsys.readouterr().out


def test_cli_spend_and_doctor_show_routing(capsys: pytest.CaptureFixture[str]) -> None:
    from jev_claud.cli import main

    assert (
        main(["ask", OBVIO, "--choice", "Setor?", "-o", "financeiro=fatura, reembolso", "-o", "suporte=erro"])
        == 0
    )
    capsys.readouterr()
    assert main(["spend"]) == 0
    out = capsys.readouterr().out
    assert "Quem respondeu as perguntas este mês" in out and "offline (grátis)" in out
    assert main(["doctor"]) == 0
    assert "Quem respondeu" in capsys.readouterr().out


def test_mcp_decide_with_images_and_box(tmp_path: Path, make_jev: Factory) -> None:
    from jev_claud.mcp import Server

    foto = tmp_path / "foto.png"
    foto.write_bytes(PNG)
    coords = BoxCoordinates(top=0.1, left=0.1, bottom=0.2, right=0.3)
    client = FakeClient(answers_for(onde=BoxAnswer(box=coords, confidence=0.9), dano=noul_answer(0.9)))
    server = Server(lambda: make_jev(client, strategy="cascade"))
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "decide",
                "arguments": {
                    "state": "pedido #7",
                    "images": [str(foto)],
                    "questions": {
                        "dano": {"type": "noul", "instructions": "Está danificado?"},
                        "onde": {"type": "box", "instructions": "Onde está o dano?"},
                    },
                },
            },
        }
    )
    assert response is not None
    import json

    result = json.loads(response["result"]["content"][0]["text"])
    assert result["decisions"]["onde"]["value"] == {"top": 0.1, "left": 0.1, "bottom": 0.2, "right": 0.3}
    assert result["decisions"]["dano"]["value"] is True
    assert client.calls[0]["state"]["texto"] == "pedido #7"
    assert len(client.calls[0]["assets"]) == 1
