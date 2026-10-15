from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from jev_claud import Store
from jev_claud.cli import main


def test_store_cache_roundtrip_and_expiry(tmp_path: object) -> None:
    store = Store(":memory:")
    store.put("k", {"a": 1})
    assert store.get("k") == {"a": 1}
    store.put("velho", {"a": 2}, ttl=-1)
    assert store.get("velho") is None
    assert store.cache_size() == 1
    assert store.clear_cache() == 1
    store.close()


def test_store_on_disk_persists(tmp_path: object) -> None:
    path = f"{tmp_path}/sub/r.sqlite3"
    first = Store(path)
    first.put("k", {"v": "ç"})
    first.record(0.01, 100)
    first.close()
    second = Store(path)
    assert second.get("k") == {"v": "ç"}
    assert second.spent() == (pytest.approx(0.01), 100, 1)
    second.close()


def test_ledger_accumulates_per_month() -> None:
    store = Store(":memory:")
    store.record(0.01, 100, month="2026-09")
    store.record(0.02, 200, month="2026-10")
    store.record(0.03, 300, month="2026-10")
    assert store.spent("2026-10") == (pytest.approx(0.05), 500, 2)
    assert [row[0] for row in store.ledger()] == ["2026-09", "2026-10"]


def test_meta_max_age() -> None:
    store = Store(":memory:")
    store.meta_put("x", "1")
    assert store.meta_get("x") == "1"
    assert store.meta_get("x", max_age=3600) == "1"
    time.sleep(0.01)
    assert store.meta_get("x", max_age=0.001) is None
    store.meta_delete("x")
    assert store.meta_get("x") is None


# -- CLI ---------------------------------------------------------------------------


def test_cli_ask_choice_offline(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "ask",
            "Fui cobrado duas vezes",
            "--choice",
            "Setor?",
            "-o",
            "financeiro=cobrança",
            "-o",
            "suporte=erro",
        ]
    )
    assert code == 0
    assert capsys.readouterr().out.startswith("financeiro")


def test_cli_ask_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["ask", "Preciso disso hoje, urgente", "--noul", "É urgente?", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["kind"] == "noul" and data["source"] == "offline"


def test_cli_ask_score(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["ask", "estou furioso", "--score", "Raiva?", "-l", "calmo", "-l", "furioso"]) == 0
    assert "via offline" in capsys.readouterr().out


def test_cli_choice_requires_options(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["ask", "x", "--choice", "Setor?"]) == 1
    assert "--option" in capsys.readouterr().err


def test_cli_doctor(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "offline" in out and "nenhuma" in out
    assert main(["doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["budget"]["zero_cost"] is True


def test_cli_spend_update_cache_models(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["spend"]) == 0
    assert "Custo zero" in capsys.readouterr().out
    assert main(["update", "--dry-run"]) == 0
    assert "pip install --upgrade typesafe-sdk" in capsys.readouterr().out
    assert main(["cache", "size"]) == 0
    assert capsys.readouterr().out.strip() == "0"
    assert main(["models"]) == 1
    assert "TYPESAFE_API_KEY" in capsys.readouterr().err


def test_cli_write_without_llm(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["write", "oi"]) == 1
    assert "Ollama" in capsys.readouterr().err


def test_cli_without_command_shows_splash(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "jev-claud setup" in out and "jev-claud -h" in out
    assert "nenhuma economia registrada ainda este mês" in out


def test_data_from_jev_claude_is_migrated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Quem vem da 0.6: a pasta `jev-claude` e o banco `jev_claude.sqlite3` mudam de nome."""
    from jev_claud.store import default_home

    monkeypatch.delenv("JEV_CLAUD_HOME", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    old = tmp_path / "jev-claude"
    old.mkdir()
    (old / "config.json").write_text('{"budget_usd": 2}', encoding="utf-8")
    legacy = Store(old / "jev_claude.sqlite3")
    legacy.record(0.5, 1000)
    legacy.close()

    home = default_home()
    assert home == tmp_path / "jev-claud" and not old.exists()
    assert (home / "config.json").read_text(encoding="utf-8") == '{"budget_usd": 2}'
    store = Store()
    assert store.path == str(home / "jev_claud.sqlite3")
    assert store.spent()[0] == pytest.approx(0.5)
    store.close()


def test_old_env_names_still_work(monkeypatch: pytest.MonkeyPatch) -> None:
    from jev_claud import Settings

    monkeypatch.setenv("JEV_CLAUDE_BUDGET_USD", "3")
    monkeypatch.setenv("JEV_CLAUDE_STRATEGY", "jev")
    settings = Settings.from_env()
    assert settings.budget_usd == 3.0 and settings.strategy == "jev"
    monkeypatch.setenv("JEV_CLAUD_BUDGET_USD", "4")  # o nome novo tem prioridade
    assert Settings.from_env().budget_usd == 4.0
