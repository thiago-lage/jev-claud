"""Esforço (blocos e profundidade), resumo em blocos e modo offline, sem rede."""

from __future__ import annotations

from typing import Any

import pytest

from conftest import FakeClient
from jev_claud import (
    CallableWriter,
    JevBackend,
    JevClaud,
    OpenAICompatibleWriter,
    Settings,
    WriterError,
    noul,
)
from jev_claud.core import summary_blocks
from jev_claud.effort import PLANS, normalize_effort, pack, split_text
from jev_claud.writers import is_local_url

# -- fatiamento --------------------------------------------------------------------------------


def test_normalize_effort_accepts_aliases_and_rejects_unknown() -> None:
    assert normalize_effort(None) == "medio"
    assert normalize_effort("  ") == "medio"
    assert normalize_effort("ALTO") == "alto"
    assert normalize_effort("low") == "baixo"
    assert normalize_effort("médio") == "medio"
    assert normalize_effort("xhigh") == "extra"
    assert normalize_effort("Máximo") == "max"
    assert normalize_effort("máx") == "max"
    assert normalize_effort("UltraCode") == "ultracode"
    with pytest.raises(ValueError, match="baixo, medio, alto, extra, max, ultracode"):
        normalize_effort("turbo")


def test_split_text_cuts_on_boundaries_and_keeps_every_word() -> None:
    paragraphs = [f"Parágrafo {i}: " + "palavra " * 40 for i in range(30)]
    text = "\n\n".join(paragraphs)
    chunks = split_text(text, 1_000)
    assert len(chunks) > 1
    assert all(len(chunk) <= 1_000 for chunk in chunks)
    assert all(chunk.startswith("Parágrafo") for chunk in chunks)  # cortou na quebra de parágrafo
    assert " ".join(chunks).split() == text.split()


def test_split_text_overlap_repeats_the_seam() -> None:
    text = " ".join(f"w{i}" for i in range(2_000))
    chunks = split_text(text, 500, overlap_chars=100)
    first_end = chunks[0].split()[-1]
    assert first_end in chunks[1].split()
    assert chunks[0].split()[0] == "w0" and chunks[-1].split()[-1] == "w1999"


def test_split_text_without_spaces_and_empty() -> None:
    assert split_text("", 100) == []
    assert split_text("   \n ", 100) == []
    assert split_text("x" * 250, 100, overlap_chars=30) == ["x" * 100, "x" * 100, "x" * 50]
    with pytest.raises(ValueError):
        split_text("abc", 0)


def test_pack_groups_by_size() -> None:
    assert pack(["aaa", "bb", "c", "dddd"], 5) == [["aaa", "bb"], ["c", "dddd"]]
    assert pack(["muito grande"], 3) == [["muito grande"]]
    assert pack([], 3) == []


def test_plans_grow_work_with_effort() -> None:
    assert PLANS["baixo"].chunk_chars > PLANS["medio"].chunk_chars > PLANS["alto"].chunk_chars
    assert PLANS["baixo"].final_tokens < PLANS["medio"].final_tokens < PLANS["max"].final_tokens
    assert not PLANS["max"].verify and PLANS["ultracode"].verify
    assert not hasattr(PLANS["max"], "final_tier")  # o esforço não escolhe modelo


def test_summary_blocks_follow_the_effort() -> None:
    text = "Uma frase com fatos. " * 1_000
    low, high = summary_blocks(text, effort="baixo"), summary_blocks(text, effort="max")
    assert len(low) < len(high)
    assert all(len(block) <= PLANS["max"].chunk_chars for block in high)


# -- um único LLM ------------------------------------------------------------------------------


class FakeEndpoint(OpenAICompatibleWriter):
    """Endpoint compatível com OpenAI que só registra as chamadas."""

    def __init__(self, calls: list[str], **kwargs: Any) -> None:
        super().__init__(kwargs.pop("base_url", "http://localhost:8080/v1"), "sessao", **kwargs)
        self.calls = calls

    def write(
        self, prompt: str, *, system: str | None = None, max_tokens: int = 800, temperature: float = 0.3
    ) -> str:
        self.calls.append(prompt)
        return f"{self.model}: ok"


def _app(writer: Any, **settings: Any) -> JevClaud:
    return JevClaud(store=":memory:", writer=writer, settings=Settings(**settings))


def test_write_uses_the_single_writer_and_appends_context() -> None:
    calls: list[str] = []
    r = _app(FakeEndpoint(calls))
    assert r.write("a", context={"k": 1}) == "sessao: ok"
    assert calls == ['a\n\nContexto:\n{\n  "k": 1\n}']
    assert r.status()["writer"] == "openai-compatible"
    r.close()


# -- resumo em blocos ----------------------------------------------------------------------------


def test_effort_changes_blocks_and_ultracode_reviews_against_sources() -> None:
    calls: list[str] = []
    r = _app(FakeEndpoint(calls))
    text = "Uma frase com fatos. " * 1_000
    r.summarize(text, effort="baixo")
    low_calls = len(calls)
    calls.clear()
    r.summarize(text, effort="max")
    max_calls = len(calls)
    assert max_calls > low_calls  # blocos menores: mais trabalho no mesmo LLM
    calls.clear()
    assert r.summarize(text, effort="ultracode") == "sessao: ok"
    assert len(calls) == max_calls + 1 and calls[-1].startswith("Revise o RESUMO")
    r.close()

    prompts: list[str] = []
    review = _app(CallableWriter(lambda p, s: prompts.append(p) or f"r{len(prompts)}"))
    assert review.summarize("Reunião: lançamos na sexta.", effort="ultracode") == "r2"
    assert prompts[-1].startswith("Revise o RESUMO") and "FONTES:\nReunião" in prompts[-1]
    assert "RESUMO:\nr1" in prompts[-1]
    review.close()


def test_summarize_merges_hierarchically_when_partials_are_long() -> None:
    prompts: list[str] = []

    def long_answer(prompt: str, system: str | None) -> str:
        prompts.append(prompt)
        return "fato " * 400  # 2 mil caracteres por parcial

    r = _app(CallableWriter(long_answer))
    r.summarize("Uma frase com fatos. " * 3_000, effort="baixo")
    intermediate = [p for p in prompts if p.startswith("Junte estes resumos parciais em um só")]
    assert intermediate, "deveria juntar em rodadas antes da junção final"
    assert prompts[-1].startswith("Junte estes resumos parciais em um resumo final")
    assert all(len(p) < PLANS["baixo"].chunk_chars + 1_000 for p in prompts)
    r.close()


def test_summarize_skips_failed_parts_and_warns() -> None:
    seen = {"n": 0}

    def flaky(prompt: str, system: str | None) -> str:
        seen["n"] += 1
        if prompt.startswith("Parte 2 "):
            raise WriterError("timeout")
        return "ok"

    r = _app(CallableWriter(flaky))
    summary = r.summarize("Uma frase com fatos. " * 1_000, effort="medio")
    assert summary.startswith("ok") and "1 de " in summary and "falharam" in summary
    r.close()


def test_summarize_raises_when_every_part_fails() -> None:
    def broken(prompt: str, system: str | None) -> str:
        raise WriterError("LLM fora do ar")

    r = _app(CallableWriter(broken))
    with pytest.raises(WriterError, match="fora do ar"):
        r.summarize("Uma frase com fatos. " * 1_000)
    with pytest.raises(ValueError, match="vazio"):
        r.summarize("   ")
    r.close()


# -- modo offline --------------------------------------------------------------------------------


def test_offline_setting_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings.from_env().offline is False
    monkeypatch.setenv("JEV_CLAUD_OFFLINE", "sim")
    assert Settings.from_env().offline is True
    monkeypatch.setenv("JEV_CLAUD_OFFLINE", "talvez")
    with pytest.raises(ValueError, match="JEV_CLAUD_OFFLINE"):
        Settings.from_env()


def test_offline_never_uses_jev() -> None:
    client = FakeClient()
    r = JevClaud(
        jev=JevBackend(client=client),
        store=":memory:",
        writer=None,
        settings=Settings(offline=True, budget_usd=10.0),
    )
    assert r.jev is None
    assert r.ask("Fui cobrado duas vezes", noul("É cobrança?")).source == "offline"
    assert client.calls == []
    assert r.status()["offline"] is True
    r.close()


def test_offline_refuses_remote_writer() -> None:
    remote = FakeEndpoint([], base_url="https://api.exemplo.com/v1")
    r = _app(remote, offline=True)
    with pytest.raises(WriterError, match="só aceita LLM local"):
        r.write("oi")
    r.close()
    lan = _app(FakeEndpoint([], base_url="http://192.168.0.10:8080/v1"), offline=True)
    assert lan.write("oi") == "sessao: ok"
    lan.close()


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("http://localhost:8080", True),
        ("http://app.localhost", True),
        ("http://127.0.0.1:9", True),
        ("http://[::1]:8080", True),
        ("http://10.0.0.5", True),
        ("192.168.1.20:8080", True),
        ("https://api.openai.com/v1", False),
        ("http://8.8.8.8", False),
        ("http://localhost.exemplo.com", False),
    ],
)
def test_is_local_url(url: str, local: bool) -> None:
    assert is_local_url(url) is local
