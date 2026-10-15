"""Assistente `jev-claud setup` com um sistema simulado: nada é instalado de verdade."""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from jev_claud import Settings
from jev_claud.cli import main
from jev_claud.config import config_path, load_config, save_config
from jev_claud.wizard import (
    CLAUDE_RULE,
    DEV_PACKAGE_SPEC,
    PACKAGE_SPEC,
    RULE_END,
    RULE_START,
    UI,
    Setup,
    System,
)

# Hook que o setup grava no settings.json (o FakeSystem usa "python" como Python).
HOOK = {"type": "command", "command": "python -m jev_claud hook pre-bash", "timeout": 10}


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
        plugin: bool = False,
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
            "plugin": plugin,
            "jev_marketplace": False,
            # Nomes até a 0.6: marketplace jev-claude, plugins jev-claude e jev, MCP jev-claude.
            "old_marketplace": False,
            "old_plugin": False,
            "old_jev": False,
            "old_mcp": False,
            "synced": False,  # jevin@synced: adicionado pelo diretório do Claude
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
            self.state["jev_marketplace" if command[4] == "thiago-lage/jev-claud" else "marketplace"] = True
        elif command[1:4] == ["plugin", "marketplace", "remove"]:
            self.state["old_marketplace"] = False
        elif command[1:3] == ["plugin", "install"]:
            self.state["plugin" if command[3] == "jevin@jev-claud" else "skill"] = True
        elif command[1:3] == ["plugin", "uninstall"]:
            names = {
                "jev-claude@jev-claude": "old_plugin",
                "jev@jev-claude": "old_jev",
                "jevin@jev-claud": "plugin",
            }
            self.state[names[command[3]]] = False
        elif command[1:3] == ["mcp", "add"]:
            self.state["mcp"] = True
        elif command[1:3] == ["mcp", "remove"]:
            self.state["old_mcp"] = False
        elif "typesafe-sdk" in command:
            self.state["sdk"] = True
        return 0

    def capture(self, command: Sequence[str]) -> tuple[int, str]:
        if command[-1] == "--version":
            return 0, "2.1.300 (Claude Code)\n"
        if list(command[1:3]) == ["plugin", "list"]:
            installed = [
                name
                for name, key in (
                    ("typesafe@typesafe-ai", "skill"),
                    ("jevin@jev-claud", "plugin"),
                    ("jev-claude@jev-claude", "old_plugin"),
                    ("jev@jev-claude", "old_jev"),
                    ("jevin@synced", "synced"),
                )
                if self.state[key]
            ]
            return 0, "\n".join(installed) + "\n" if installed else "Nenhum plugin\n"
        if list(command[1:4]) == ["plugin", "marketplace", "list"]:
            names = ["anthropic"]
            names += ["typesafe-ai"] if self.state["marketplace"] else []
            names += ["jev-claud"] if self.state["jev_marketplace"] else []
            names += ["jev-claude"] if self.state["old_marketplace"] else []
            return 0, "\n".join(names) + "\n"
        if list(command[1:3]) == ["mcp", "get"]:
            on = self.state["mcp"] if command[3] == "jev-claud" else self.state["old_mcp"]
            return (0, f"{command[3]}: connected\n") if on else (1, "No MCP server named\n")
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
        plugin=True,
    )
    rule = system.home / ".claude" / "CLAUDE.md"
    rule.parent.mkdir(parents=True)
    rule.write_text(f"# Minhas regras\n\n{CLAUDE_RULE}", encoding="utf-8")
    group = {"matcher": "Bash", "hooks": [HOOK]}
    (rule.parent / "settings.json").write_text(
        json.dumps({"hooks": {"PreToolUse": [group]}}), encoding="utf-8"
    )
    shim = system.home / ".local" / "bin" / "jev-claud"
    shim.parent.mkdir(parents=True)
    shim.write_text("#!/bin/sh\n", encoding="utf-8")
    return system


def test_check_mode_only_reports(tmp_path: Path) -> None:
    system = FakeSystem(tmp_path)
    ui, out = make_ui()
    report = Setup(system=system, ui=ui, version="0.2.0").run("check")
    assert not report.ok
    assert system.commands == [] and system.spawned == []
    text = out.getvalue()
    assert "Bem-vindo ao jev-claud setup" in text and "Ollama" in text and "Ainda falta" in text
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
    assert "/bin/claude mcp add -s user jev-claud -- python -m jev_claud mcp" in commands
    assert "/bin/claude plugin marketplace add thiago-lage/jev-claud" in commands
    assert "/bin/claude plugin install jevin@jev-claud" in commands
    shim = (system.home / ".local" / "bin" / "jev-claud").read_bytes()
    assert shim == b'#!/bin/sh\nexec "python" -m jev_claud "$@"\n'
    settings = json.loads((system.home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings == {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [HOOK]}]}}
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
    monkeypatch.setenv("JEV_CLAUD_BUDGET_USD", "0")
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
    assert "█" in text and "╭" in text and "✔" in text and "\x1b[" in text  # mascote em blocos


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

    monkeypatch.setattr("jev_claud.cli.run_setup", fake_run_setup)
    assert main(["setup"]) == 0
    assert main(["setup", "--yes", "--model", "qwen2.5:3b"]) == 0
    assert main(["setup", "--check"]) == 0
    assert calls == [("interactive", None), ("yes", "qwen2.5:3b"), ("check", None)]


def test_cli_update_upgrades_itself_when_not_editable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("jev_claud.cli._installed_editable", lambda: False)
    assert main(["update", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "typesafe-sdk" in out and f"--upgrade {PACKAGE_SPEC}\n" in out
    assert PACKAGE_SPEC == "jev-claud[jev]"  # versão lançada no PyPI
    assert main(["update", "--dry-run", "--dev"]) == 0
    assert DEV_PACKAGE_SPEC in capsys.readouterr().out


def test_cli_update_refuses_to_replace_running_windows_exe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("jev_claud.cli._installed_editable", lambda: False)
    monkeypatch.setattr("jev_claud.cli._running_windows_launcher", lambda: True)
    assert main(["update", "--dry-run"]) == 1
    captured = capsys.readouterr()
    assert "-m jev_claud update" in captured.err
    assert "pip install" not in captured.out  # nada foi tocado


def test_config_used_by_jevclaude(tmp_path: Path) -> None:
    from jev_claud import JevClaud

    save_config({"typesafe_api_key": "ts-arquivo", "budget_usd": 3})
    with JevClaud(store=":memory:", writer=None) as r:
        assert r.jev is not None
        assert r.budget.monthly_usd == 3.0
        assert r.status()["jev"]["api_key"] is True
    assert json.loads(config_path().read_text(encoding="utf-8"))["typesafe_api_key"] == "ts-arquivo"


def test_old_rule_block_is_replaced_in_place(tmp_path: Path) -> None:
    system = FakeSystem(tmp_path, ollama=True, server=True, models=["llama3.2:latest"])
    rule = system.home / ".claude" / "CLAUDE.md"
    rule.parent.mkdir(parents=True)
    rule.write_text(f"# Antes\n\n{RULE_START}\nregra antiga\n{RULE_END}\n\n# Depois\n", encoding="utf-8")
    ui, _ = make_ui()
    Setup(system=system, ui=ui).run("yes")
    text = rule.read_text(encoding="utf-8")
    assert "regra antiga" not in text
    assert text == f"# Antes\n\n{CLAUDE_RULE}\n# Depois\n"
    assert "implemente direto" in text


def test_bash_shim_only_on_windows(tmp_path: Path) -> None:
    system = FakeSystem(tmp_path, "linux", ollama=True, server=True, models=["llama3.2:latest"], claude=True)
    ui, _ = make_ui()
    report = Setup(system=system, ui=ui).run("yes")
    assert report.results["plugin"] is True and report.results["hook"] is True
    assert not (system.home / ".local" / "bin" / "jev-claud").exists()


def test_hook_goes_to_settings_preserving_the_rest(tmp_path: Path) -> None:
    """O hook fica no settings.json do usuário: troca versões anteriores dele e não toca no resto."""
    system = FakeSystem(tmp_path, "linux", ollama=True, server=True, models=["llama3.2:latest"], claude=True)
    settings = system.home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    lint = {"type": "command", "command": "meu-lint"}
    stop = [{"hooks": [{"type": "command", "command": "notificar"}]}]
    old_venv = {"type": "command", "command": "/venv-velho/bin/python -m jev_claude hook pre-bash"}
    on_path = {"type": "command", "command": "jev-claud hook pre-bash"}
    before = {
        "model": "opus",
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [lint, old_venv]},
                {"matcher": "Bash", "hooks": [on_path]},
            ],
            "Stop": stop,
        },
    }
    settings.write_text(json.dumps(before), encoding="utf-8")
    ui, _ = make_ui()
    assert Setup(system=system, ui=ui)._check_hook() == (False, "aponta para outra instalação do jev-claud")

    assert Setup(system=system, ui=ui).run("yes").results["hook"] is True
    after = json.loads(settings.read_text(encoding="utf-8"))
    assert after == {
        "model": "opus",
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [lint]}, {"matcher": "Bash", "hooks": [HOOK]}],
            "Stop": stop,
        },
    }
    assert Setup(system=system, ui=ui)._check_hook()[0] is True
    Setup(system=system, ui=ui).run("yes")  # rodar de novo não duplica
    assert json.loads(settings.read_text(encoding="utf-8")) == after


def test_invalid_settings_are_left_alone(tmp_path: Path) -> None:
    system = FakeSystem(tmp_path, "linux", ollama=True, server=True, models=["llama3.2:latest"], claude=True)
    settings = system.home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{ quebrado", encoding="utf-8")
    ui, out = make_ui()
    assert Setup(system=system, ui=ui).run("yes").results["hook"] is False
    assert settings.read_text(encoding="utf-8") == "{ quebrado"
    assert "não está no formato esperado" in out.getvalue()


def test_everything_named_jev_claude_is_migrated(tmp_path: Path) -> None:
    """Quem vem da 0.6: marketplace, plugins e MCP com o nome antigo viram jev-claud/jevin."""
    system = FakeSystem(tmp_path, "linux", ollama=True, server=True, models=["llama3.2:latest"], claude=True)
    system.state.update(old_marketplace=True, old_plugin=True, old_jev=True, old_mcp=True)
    ui, out = make_ui()
    report = Setup(system=system, ui=ui).run("yes")
    commands = [" ".join(c) for c in system.commands]
    assert "nome antigo (jev-claude@jev-claude)" in out.getvalue()
    assert "registrado com o nome antigo (jev-claude)" in out.getvalue()
    assert "/bin/claude mcp remove -s user jev-claude" in commands
    assert "/bin/claude mcp add -s user jev-claud -- python -m jev_claud mcp" in commands
    assert "/bin/claude plugin marketplace add thiago-lage/jev-claud" in commands
    assert "/bin/claude plugin install jevin@jev-claud" in commands
    assert "/bin/claude plugin uninstall jev-claude@jev-claude" in commands
    assert "/bin/claude plugin uninstall jev@jev-claude" in commands
    assert "/bin/claude plugin marketplace remove jev-claude" in commands
    assert report.results["plugin"] is True and report.results["mcp"] is True
    assert not any(system.state[k] for k in ("old_marketplace", "old_plugin", "old_jev", "old_mcp"))


def test_old_rule_markers_are_replaced(tmp_path: Path) -> None:
    system = FakeSystem(tmp_path, "linux", ollama=True, server=True, models=["llama3.2:latest"])
    rule = system.home / ".claude" / "CLAUDE.md"
    rule.parent.mkdir(parents=True)
    old = "<!-- jev-claude:start -->\nregra da 0.6\n<!-- jev-claude:end -->"
    rule.write_text(f"# Minhas\n\n{old}\n\n# Fim\n", encoding="utf-8")
    ui, _ = make_ui()
    Setup(system=system, ui=ui).run("yes")
    text = rule.read_text(encoding="utf-8")
    assert "regra da 0.6" not in text and "jev-claude:start" not in text
    assert text == f"# Minhas\n\n{CLAUDE_RULE}\n# Fim\n"


def test_windows_shims_cover_both_names(tmp_path: Path) -> None:
    system = FakeSystem(tmp_path, ollama=True, server=True, models=["llama3.2:latest"], claude=True)
    ui, _ = make_ui()
    Setup(system=system, ui=ui).run("yes")
    for name in ("jev-claud", "jev-claude"):
        shim = (system.home / ".local" / "bin" / name).read_bytes()
        assert shim == b'#!/bin/sh\nexec "python" -m jev_claud "$@"\n'


def test_plugin_from_the_claude_directory_counts_as_installed(tmp_path: Path) -> None:
    system = FakeSystem(tmp_path, "linux", ollama=True, server=True, models=["llama3.2:latest"], claude=True)
    system.state["synced"] = True
    ui, out = make_ui()
    report = Setup(system=system, ui=ui).run("yes")
    assert report.results["plugin"] is True
    assert "jevin@synced (diretório do Claude)" in out.getvalue()
    assert not any(c[1:3] == ["plugin", "install"] and c[3] == "jevin@jev-claud" for c in system.commands)


def test_marketplace_copy_is_removed_when_the_directory_one_exists(tmp_path: Path) -> None:
    """Com o Jevin do diretório ativo, a cópia do marketplace impede o carregamento: o setup a remove."""
    system = FakeSystem(tmp_path, "linux", ollama=True, server=True, models=["llama3.2:latest"], claude=True)
    system.state.update(synced=True, plugin=True)
    ui, out = make_ui()
    report = Setup(system=system, ui=ui).run("yes")
    assert "duplicado: o jevin@synced do diretório só carrega sem o jevin@jev-claud" in out.getvalue()
    assert "/bin/claude plugin uninstall jevin@jev-claud" in [" ".join(c) for c in system.commands]
    assert report.results["plugin"] is True and system.state["plugin"] is False


def agents_system(tmp_path: Path) -> FakeSystem:
    """Claude Code pronto; Cursor e Codex instalados (CODEX_HOME aponta para a pasta de teste)."""
    system = FakeSystem(tmp_path, "linux", ollama=True, server=True, models=["llama3.2:latest"], claude=True)
    system.tools |= {"cursor", "codex"}
    system.env = {**system.env, "CODEX_HOME": str(tmp_path / "codex")}
    return system


CURSOR_ENTRY = {"command": "python", "args": ["-m", "jev_claud", "mcp"]}
CODEX_BLOCK = '[mcp_servers.jev-claud]\ncommand = "python"\nargs = ["-m", "jev_claud", "mcp"]\n'


def test_cursor_and_codex_steps_only_when_installed(tmp_path: Path) -> None:
    system = FakeSystem(tmp_path, "linux", claude=True)
    ui, _ = make_ui()
    assert not {"cursor_mcp", "codex_mcp", "codex_rule"} & {
        s.key for s in Setup(system=system, ui=ui).steps()
    }
    (system.home / ".cursor").mkdir(parents=True)  # o Cursor cria essa pasta, mesmo sem o comando no PATH
    assert "cursor_mcp" in {s.key for s in Setup(system=system, ui=ui).steps()}
    system.tools.add("codex")
    assert {"codex_mcp", "codex_rule"} <= {s.key for s in Setup(system=system, ui=ui).steps()}


def test_cursor_mcp_is_registered_preserving_the_rest(tmp_path: Path) -> None:
    system = agents_system(tmp_path)
    mcp = system.home / ".cursor" / "mcp.json"
    mcp.parent.mkdir(parents=True)
    other = {"command": "npx", "args": ["outro-servidor"]}
    mcp.write_text(json.dumps({"mcpServers": {"outro": other}, "extra": 1}), encoding="utf-8")
    ui, _ = make_ui()
    assert Setup(system=system, ui=ui).run("yes").results["cursor_mcp"] is True
    after = json.loads(mcp.read_text(encoding="utf-8"))
    assert after == {"mcpServers": {"outro": other, "jev-claud": CURSOR_ENTRY}, "extra": 1}

    mcp.write_text("{ quebrado", encoding="utf-8")
    ui, out = make_ui()
    assert Setup(system=system, ui=ui).run("yes").results["cursor_mcp"] is False
    assert mcp.read_text(encoding="utf-8") == "{ quebrado"
    assert "não está no formato esperado" in out.getvalue()


def test_codex_gets_the_mcp_table_and_the_rule(tmp_path: Path) -> None:
    system = agents_system(tmp_path)
    config = tmp_path / "codex" / "config.toml"
    config.parent.mkdir()
    mine = 'model = "gpt-5"\n\n[mcp_servers.outro]\ncommand = "npx"\nargs = ["x"]\n'
    config.write_text(mine, encoding="utf-8")
    ui, _ = make_ui()
    report = Setup(system=system, ui=ui).run("yes")
    assert report.results["codex_mcp"] is True and report.results["codex_rule"] is True
    assert config.read_text(encoding="utf-8") == mine + "\n" + CODEX_BLOCK
    assert CLAUDE_RULE in (tmp_path / "codex" / "AGENTS.md").read_text(encoding="utf-8")
    Setup(system=system, ui=ui).run("yes")  # rodar de novo não duplica
    assert config.read_text(encoding="utf-8") == mine + "\n" + CODEX_BLOCK


def test_codex_old_entry_is_replaced_in_place(tmp_path: Path) -> None:
    system = agents_system(tmp_path)
    config = tmp_path / "codex" / "config.toml"
    config.parent.mkdir()
    old = '[mcp_servers."jev-claud"]\ncommand = "/velho/python"\nargs = ["-m", "jev_claud", "mcp"]\n'
    config.write_text(old + "\n[outra]\nchave = 1\n", encoding="utf-8")
    ui, _ = make_ui()
    setup = Setup(system=system, ui=ui)
    assert setup._check_codex_mcp() == (False, "aponta para outra instalação do jev-claud")
    assert setup._install_codex_mcp() is True
    assert config.read_text(encoding="utf-8") == CODEX_BLOCK + "\n[outra]\nchave = 1\n"


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib só existe a partir do Python 3.11")
def test_codex_config_that_would_break_is_left_alone(tmp_path: Path) -> None:
    import tomllib

    system = agents_system(tmp_path)
    system.python = "C:\\Users\\Ana Lima\\venv\\Scripts\\python.exe"  # barras invertidas e espaço
    config = tmp_path / "codex" / "config.toml"
    config.parent.mkdir()
    ui, _ = make_ui()
    assert Setup(system=system, ui=ui)._install_codex_mcp() is True
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["mcp_servers"]["jev-claud"]["command"] == system.python

    inline = 'mcp_servers = { outro = { command = "npx" } }\n'  # acrescentar a tabela quebraria o TOML
    config.write_text(inline, encoding="utf-8")
    ui, out = make_ui()
    assert Setup(system=system, ui=ui)._install_codex_mcp() is False
    assert config.read_text(encoding="utf-8") == inline
    assert "não está no formato esperado" in out.getvalue()
