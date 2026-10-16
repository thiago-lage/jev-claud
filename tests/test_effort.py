"""Esforço, camadas de modelo local, resumo em blocos e modo offline, sem rede."""

from __future__ import annotations

import logging
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
from jev_claud.effort import PLANS, normalize_effort, pack, split_text
from jev_claud.writers import auto_writer, is_local_url

# -- fatiamento --------------------------------------------------------------------------------


def test_normalize_effort_accepts_aliases_and_rejects_unknown() -> None:
    assert normalize_effort(None) == "medio"
    assert normalize_effort("  ") == "medio"
    assert normalize_effort("ALTO") == "alto"
    assert normalize_effort("low") == "baixo"
    assert normalize_effort("médio") == "medio"
    with pytest.raises(ValueError, match="baixo, medio ou alto"):
        normalize_effort("máximo")


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
    assert PLANS["baixo"].final_tier == "rapido" and PLANS["alto"].final_tier == "forte"


# -- camadas de modelo -------------------------------------------------------------------------


class FakeEndpoint(OpenAICompatibleWriter):
    """Endpoint compatível com OpenAI que só registra o modelo usado em cada chamada."""

    def __init__(self, calls: list[str], broken: frozenset[str] = frozenset(), **kwargs: Any) -> None:
        super().__init__(kwargs.pop("base_url", "http://localhost:11434/v1"), "padrao-7b", **kwargs)
        self.calls = calls
        self.broken = broken

    def write(
        self, prompt: str, *, system: str | None = None, max_tokens: int = 800, temperature: float = 0.3
    ) -> str:
        self.calls.append(self.model)
        if self.model in self.broken:
            raise WriterError(f"model '{self.model}' not found")
        return f"{self.model}: ok"


def _app(writer: Any, **settings: Any) -> JevClaud:
    return JevClaud(store=":memory:", writer=writer, settings=Settings(**settings))


def test_tiers_swap_only_the_model() -> None:
    calls: list[str] = []
    r = _app(FakeEndpoint(calls), ollama_model_fast="rapido-1b", ollama_model_strong="forte-14b")
    assert r.write("a", tier="rapido") == "rapido-1b: ok"
    assert r.write("b", tier="forte") == "forte-14b: ok"
    assert r.write("c") == "padrao-7b: ok"
    assert r.tier_models() == {"rapido": "rapido-1b", "padrao": "padrao-7b", "forte": "forte-14b"}
    assert calls == ["rapido-1b", "forte-14b", "padrao-7b"]
    r.close()


def test_tier_without_model_uses_default() -> None:
    calls: list[str] = []
    r = _app(FakeEndpoint(calls))
    r.write("a", tier="rapido")
    r.write("b", tier="forte")
    assert calls == ["padrao-7b", "padrao-7b"]
    r.close()


def test_broken_tier_falls_back_once_and_is_remembered(caplog: pytest.LogCaptureFixture) -> None:
    calls: list[str] = []
    r = _app(FakeEndpoint(calls, frozenset({"forte-14b"})), ollama_model_strong="forte-14b")
    with caplog.at_level(logging.WARNING, logger="jev_claud"):
        assert r.write("a", tier="forte") == "padrao-7b: ok"
    assert "forte-14b falhou" in caplog.text
    assert r.write("b", tier="forte") == "padrao-7b: ok"
    assert calls == ["forte-14b", "padrao-7b", "padrao-7b"]  # não tenta o modelo quebrado de novo
    r.close()


def test_default_model_failure_still_raises() -> None:
    r = _app(FakeEndpoint([], frozenset({"padrao-7b"})))
    with pytest.raises(WriterError):
        r.write("a", tier="rapido")
    r.close()


# -- resumo em blocos ----------------------------------------------------------------------------


def test_summarize_routes_parts_and_merge_to_their_tiers() -> None:
    calls: list[str] = []
    r = _app(FakeEndpoint(calls), ollama_model_fast="rapido-1b", ollama_model_strong="forte-14b")
    text = "Uma frase com fatos. " * 1_000
    r.summarize(text, effort="medio")
    assert set(calls[:-1]) == {"rapido-1b"} and calls[-1] == "padrao-7b"
    calls.clear()
    r.summarize(text, effort="alto")
    assert set(calls[:-1]) == {"padrao-7b"} and calls[-1] == "forte-14b"
    r.close()


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
        raise WriterError("Ollama fora do ar")

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
    lan = _app(FakeEndpoint([], base_url="http://192.168.0.10:11434/v1"), offline=True)
    assert lan.write("oi") == "padrao-7b: ok"
    lan.close()


def test_auto_writer_offline_ignores_remote_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_CLAUD_LLM_BASE_URL", "https://api.exemplo.com/v1")
    monkeypatch.setenv("JEV_CLAUD_LLM_MODEL", "algum")
    assert isinstance(auto_writer(), OpenAICompatibleWriter)
    assert auto_writer(local_only=True) is None  # o Ollama do conftest recusa a conexão
    monkeypatch.setenv("JEV_CLAUD_LLM_BASE_URL", "http://127.0.0.1:1234/v1")
    writer = auto_writer(local_only=True)
    assert isinstance(writer, OpenAICompatibleWriter) and writer.base_url == "http://127.0.0.1:1234/v1"
    monkeypatch.delenv("JEV_CLAUD_LLM_BASE_URL")
    monkeypatch.setenv("OLLAMA_HOST", "https://ollama.exemplo.com")
    assert auto_writer(local_only=True) is None


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("http://localhost:11434", True),
        ("http://app.localhost", True),
        ("http://127.0.0.1:9", True),
        ("http://[::1]:11434", True),
        ("http://10.0.0.5", True),
        ("192.168.1.20:11434", True),
        ("https://api.openai.com/v1", False),
        ("http://8.8.8.8", False),
        ("http://localhost.exemplo.com", False),
    ],
)
def test_is_local_url(url: str, local: bool) -> None:
    assert is_local_url(url) is local
