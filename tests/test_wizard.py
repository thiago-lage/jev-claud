"""Assistente `jev-claude setup` com um sistema simulado: nada é instalado de verdade."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from jev_claude import Settings
from jev_claude.cli import main
from jev_claude.config import config_path, load_config, save_config
from jev_claude.wizard import PACKAGE_SPEC, RULE_START, UI, Setup, System


class FakeSystem(System):
    def __init__(
        self,
        tmp_path: Path,
        platform: str = "win32",
        *,
        ollama: bool = False,
        server: bool = False,
        models: Sequence[str] = (),
        claude: bool = False,
        skill: bool = False,
        sdk: bool = True,
        in_venv: bool = True,
        tools: Sequence[str] = ("winget", "brew"),
        mcp: bool = False,
    ) -> None:
        self.platform = platform
        self.home = tmp_path / "home"
        self.env = {"LOCALAPPDATA": str(tmp_path / "local")}
        self.python = "python"
        self.in_venv = in_venv
        self.state: dict[str, Any] = {
            "ollama": ollama,
            "server": server,
            "models": list(models),
            "claude": claude,
            "skill": skill,
            "sdk": sdk,
            "marketplace": False,
            "mcp": mcp,
        }
        self.tools = set(tools)
        self.commands: list[list[str]] = []
        self.spawned: list[list[str]] = []

    def which(self, name: str) -> str | None:
        if name in ("ollama", "claude"):
            return f"/bin/{name}" if self.state[name] else None
        return f"/usr/bin/{name}" if name in self.tools else None

    def run(self, command: Sequence[str]) -> int:
        command = list(command)
        self.commands.append(command)
        joined = " ".join(command)
        if (
            "Ollama.Ollama" in joined
            or command[:2] == ["brew", "install"]
            or "ollama.com/install.sh" in joined
        ):
            self.state["ollama"] = True
        elif "pull" in command:
            name = command[-1]
            self.state["models"].append(name if ":" in name else f"{name}:latest")
        elif "claude.ai/install" in joined:
            self.state["claude"] = True
        elif command[1:4] == ["plugin", "marketplace", "add"]:
            self.state["marketplace"] = True
        elif command[1:3] == ["plugin", "install"]:
            self.state["skill"] = True
        elif command[1:3] == ["mcp", "add"]:
            self.state["mcp"] = True
        elif "typesafe-sdk" in command:
            self.state["sdk"] = True
        return 0

    def capture(self, command: Sequence[str]) -> tuple[int, str]:
        if command[-1] == "--version":
            return 0, "2.1.300 (Claude Code)\n"
        if list(command[1:3]) == ["plugin", "list"]:
            return 0, "typesafe@typesafe-ai\n" if self.state["skill"] else "Nenhum plugin\n"
        if list(command[1:4]) == ["plugin", "marketplace", "list"]:
            return 0, "typesafe-ai\n" if self.state["marketplace"] else "anthropic\n"
        if list(command[1:4]) == ["mcp", "get", "jev-claude"]:
            return (0, "jev-claude: connected\n") if self.state["mcp"] else (1, "No MCP server named\n")
        return 1, ""

    def spawn(self, command: Sequence[str]) -> None:
        self.spawned.append(list(command))
        self.state["server"] = True

    def ollama_models(self) -> list[str] | None:
        return list(self.state["models"]) if self.state["server"] else None

    def sdk_installed(self) -> bool:
        return bool(self.state["sdk"])

    def sleep(self, seconds: float) -> None:
        pass


def make_ui(
    answers: Sequence[str] = (), secret: str = "", interactive: bool = True
) -> tuple[UI, io.StringIO]:
    out = io.StringIO()
    replies: Iterator[str] = iter(answers)
    ui = UI(
        out,
        ask=lambda _prompt: next(replies),
        secret=lambda _prompt: secret,
        interactive=interactive,
        color=False,
    )
    return ui, out


def ready_system(tmp_path: Path) -> FakeSystem:
    system = FakeSystem(
        tmp_path,
        ollama=True,
        server=True,
        models=["llama3.2:latest"],
        claude=True,
        skill=True,
        sdk=True,
        mcp=True,
    )
    rule = system.home / ".claude" / "CLAUDE.md"
    rule.parent.mkdir(parents=True)
    rule.write_text(f"# Minhas regras\n\n{RULE_START}\n", encoding="utf-8")
    return system


def test_check_mode_only_reports(tmp_path: Path) -> None:
    system = FakeSystem(tmp_path)
    ui, out = make_ui()
    report = Setup(system=system, ui=ui, version="0.2.0").run("check")
    assert not report.ok
    assert system.commands == [] and system.spawned == []
    text = out.getvalue()
    assert "Bem-vindo ao jev-claude setup" in text and "Ollama" in text and "Ainda falta" in text
    assert "setup_version" not in load_config()  # --check não grava nada


def test_yes_mode_sets_up_everything_on_windows(tmp_path: Path) -> None:
    system = FakeSystem(tmp_path)
    ui, out = make_ui()
    report = Setup(system=system, ui=ui, version="0.2.0").run("yes")

    assert report.ok, out.getvalue()
    commands = [" ".join(c) for c in system.commands]
    assert any(c.startswith("winget install -e --id Ollama.Ollama") for c in commands)
    assert "/bin/ollama pull llama3.2" in commands
    assert any("irm https://claude.ai/install.ps1 | iex" in c for c in commands)
    assert "/bin/claude plugin marketplace add typesafe-ai/skills" in commands
    assert "/bin/claude plugin install typesafe@typesafe-ai" in commands
    assert "/bin/claude mcp add -s user jev-claude -- python -m jev_claude mcp" in commands
    assert system.spawned == [["/bin/ollama", "serve"]]
    assert report.results["jev_key"] is False  # exige digitar: fica para o modo interativo
    assert RULE_START in (system.home / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    assert load_config()["setup_version"] == "0.2.0"


@pytest.mark.parametrize(
    ("platform", "ollama_cmd", "claude_cmd"),
    [
        ("darwin", "brew install ollama", "curl -fsSL https://claude.ai/install.sh | bash"),
        (
            "linux",
            "curl -fsSL https://ollama.com/install.sh | sh",
            "curl -fsSL https://claude.ai/install.sh | bash",
        ),
    ],
)
def test_platform_installers(tmp_path: Path, platform: str, ollama_cmd: str, claude_cmd: str) -> None:
    system = FakeSystem(tmp_path, platform)
    ui, _ = make_ui()
    Setup(system=system, ui=ui).run("yes")
    commands = [" ".join(c) for c in system.commands]
    assert any(ollama_cmd in c for c in commands)
    assert any(claude_cmd in c for c in commands)


def test_missing_package_manager_is_explained(tmp_path: Path) -> None:
    system = FakeSystem(tmp_path, "win32", tools=())
    ui, out = make_ui()
    report = Setup(system=system, ui=ui).run("yes")
    assert not report.ok
    assert "https://ollama.com/download" in out.getvalue()


def test_interactive_declining_installs_nothing(tmp_path: Path) -> None:
    system = FakeSystem(tmp_path)
    ui, out = make_ui(answers=["n"] * 10)
    report = Setup(system=system, ui=ui).run("interactive")
    assert system.commands == []
    assert "pulado." in out.getvalue()
    assert not (system.home / ".claude" / "CLAUDE.md").exists()
    assert not report.ok


def test_interactive_saves_key_and_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    system = ready_system(tmp_path)
    ui, out = make_ui(answers=["s", "2,5"], secret="ts-chave-teste")
    report = Setup(system=system, ui=ui).run("interactive")

    assert report.results["jev_key"] is True
    assert "teto de US$ 2.50/mês" in out.getvalue()
    assert "ts-chave-teste" not in out.getvalue()  # a chave nunca aparece na tela
    settings = Settings.from_env()
    assert settings.typesafe_api_key == "ts-chave-teste" and settings.budget_usd == 2.5
    assert "ts-chave-teste" not in repr(settings)
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-do-ambiente")
    monkeypatch.setenv("JEV_CLAUDE_BUDGET_USD", "0")
    settings = Settings.from_env()
    assert settings.typesafe_api_key == "ts-do-ambiente" and settings.budget_usd == 0.0


def test_skipping_key_keeps_zero_cost(tmp_path: Path) -> None:
    system = ready_system(tmp_path)
    ui, _ = make_ui(answers=["s"], secret="")
    report = Setup(system=system, ui=ui).run("interactive")
    assert report.results["jev_key"] is False
    assert report.ok  # a chave é opcional
    assert "typesafe_api_key" not in load_config()


def test_global_python_never_gets_sdk_automatically(tmp_path: Path) -> None:
    system = FakeSystem(tmp_path, sdk=False, in_venv=False)
    ui, out = make_ui()
    Setup(system=system, ui=ui).run("yes")
    assert not any("typesafe-sdk" in c for c in system.commands)
    assert "precisa de você" in out.getvalue()


def test_sdk_installs_inside_isolated_env(tmp_path: Path) -> None:
    system = FakeSystem(tmp_path, sdk=False, in_venv=True)
    ui, _ = make_ui()
    Setup(system=system, ui=ui).run("yes")
    assert ["python", "-m", "pip", "install", "--upgrade", "typesafe-sdk"] in system.commands


def test_rule_is_appended_once_preserving_content(tmp_path: Path) -> None:
    system = FakeSystem(tmp_path, ollama=True, server=True, models=["llama3.2:latest"])
    rule = system.home / ".claude" / "CLAUDE.md"
    rule.parent.mkdir(parents=True)
    rule.write_text("# Minhas regras", encoding="utf-8")
    for _ in range(2):
        ui, _ = make_ui()
        Setup(system=system, ui=ui).run("yes")
    text = rule.read_text(encoding="utf-8")
    assert text.startswith("# Minhas regras\n\n")
    assert text.count(RULE_START) == 1


def test_custom_model_is_pulled_and_remembered(tmp_path: Path) -> None:
    system = FakeSystem(tmp_path, ollama=True, server=True)
    ui, _ = make_ui()
    Setup(system=system, ui=ui, ollama_model="qwen2.5:3b").run("yes")
    assert ["/bin/ollama", "pull", "qwen2.5:3b"] in system.commands
    assert load_config()["ollama_model"] == "qwen2.5:3b"
    assert Settings.from_env().ollama_model == "qwen2.5:3b"


def test_windows_starts_ollama_app_when_present(tmp_path: Path) -> None:
    system = FakeSystem(tmp_path)
    app_dir = tmp_path / "local" / "Programs" / "Ollama"
    app_dir.mkdir(parents=True)
    (app_dir / "ollama.exe").write_text("")
    (app_dir / "ollama app.exe").write_text("")
    system.tools = set()
    system.state["ollama"] = False  # só encontrado pelo caminho padrão, não pelo PATH
    ui, _ = make_ui()
    Setup(system=system, ui=ui).run("yes")
    assert system.spawned == [[str(app_dir / "ollama app.exe")]]


def test_non_interactive_terminal_only_checks(tmp_path: Path) -> None:
    system = FakeSystem(tmp_path)
    ui, out = make_ui(interactive=False)
    Setup(system=system, ui=ui).run("interactive")
    assert system.commands == []
    assert "--yes" in out.getvalue()


def test_ui_uses_unicode_when_terminal_supports_it() -> None:
    out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    ui = UI(out, interactive=False, color=True)
    ui.banner("0.2.0")
    ui.status("ok", "Tudo certo")
    out.flush()
    text = out.buffer.getvalue().decode("utf-8")  # type: ignore[attr-defined]
    assert "✻" in text and "╭" in text and "✔" in text and "\x1b[" in text


def test_ui_confirm_defaults_and_eof() -> None:
    ui, _ = make_ui(answers=["", "N", "sim"])
    assert ui.confirm("?") is True
    assert ui.confirm("?") is False
    assert ui.confirm("?", default=False) is True

    def eof(_prompt: str) -> str:
        raise EOFError

    assert UI(io.StringIO(), ask=eof, interactive=True).confirm("?") is False


# -- configuração e CLI ---------------------------------------------------------------


def test_config_file_roundtrip_and_validation(tmp_path: Path) -> None:
    save_config({"budget_usd": 1.0, "model": "preview"})
    save_config({"model": None})
    assert load_config() == {"budget_usd": 1.0}
    with pytest.raises(ValueError):
        save_config({"desconhecida": 1})
    config_path().write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config()
    config_path().write_text("{quebrado", encoding="utf-8")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_cli_setup_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str | None]] = []

    def fake_run_setup(mode: str, *, ollama_model: str | None = None, version: str = "") -> int:
        calls.append((mode, ollama_model))
        return 0

    monkeypatch.setattr("jev_claude.cli.run_setup", fake_run_setup)
    assert main(["setup"]) == 0
    assert main(["setup", "--yes", "--model", "qwen2.5:3b"]) == 0
    assert main(["setup", "--check"]) == 0
    assert calls == [("interactive", None), ("yes", "qwen2.5:3b"), ("check", None)]


def test_cli_update_upgrades_itself_when_not_editable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("jev_claude.cli._installed_editable", lambda: False)
    assert main(["update", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "typesafe-sdk" in out and PACKAGE_SPEC in out


def test_cli_update_refuses_to_replace_running_windows_exe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("jev_claude.cli._installed_editable", lambda: False)
    monkeypatch.setattr("jev_claude.cli._running_windows_launcher", lambda: True)
    assert main(["update", "--dry-run"]) == 1
    captured = capsys.readouterr()
    assert "-m jev_claude update" in captured.err
    assert "pip install" not in captured.out  # nada foi tocado


def test_config_used_by_jevclaude(tmp_path: Path) -> None:
    from jev_claude import JevClaude

    save_config({"typesafe_api_key": "ts-arquivo", "budget_usd": 3})
    with JevClaude(store=":memory:", writer=None) as r:
        assert r.jev is not None
        assert r.budget.monthly_usd == 3.0
        assert r.status()["jev"]["api_key"] is True
    assert json.loads(config_path().read_text(encoding="utf-8"))["typesafe_api_key"] == "ts-arquivo"
