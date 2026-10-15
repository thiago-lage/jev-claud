"""Compactação de saídas e hook `PreToolUse`: tudo local, sem rede nem LLM."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jev_claude.compact import (
    MIN_LINES,
    compact_text,
    pre_bash_hook,
    run_command,
    run_filter,
    run_hook,
    save_log,
    wrap_command,
)


def noisy_output(failures: int = 1) -> str:
    lines = [f"tests/test_mod.py::test_{i} PASSED" for i in range(300)]
    for n in range(failures):
        lines[150 + n * 40] = f"E       AssertionError: esperado 2, veio {n}"
    lines.append("===== 1 failed, 299 passed in 1.23s =====")
    return "\n".join(lines)


def test_short_output_passes_untouched() -> None:
    text = "\n".join(f"linha {i}" for i in range(MIN_LINES))
    assert compact_text(text) == (text, False)


def test_long_output_keeps_head_tail_and_errors() -> None:
    compacted, changed = compact_text(noisy_output(failures=2))
    assert changed
    lines = compacted.splitlines()
    assert len(lines) < 80
    assert lines[0] == "tests/test_mod.py::test_0 PASSED"
    assert "===== 1 failed, 299 passed in 1.23s =====" in lines
    assert "E       AssertionError: esperado 2, veio 0" in lines
    assert "E       AssertionError: esperado 2, veio 1" in lines
    assert "tests/test_mod.py::test_149 PASSED" in lines  # contexto antes do erro
    assert any(line.startswith("… [") and "linhas omitidas" in line for line in lines)


def test_very_long_lines_are_cut() -> None:
    text = "\n".join(["x" * 5000] + [f"ok {i}" for i in range(200)])
    compacted, _ = compact_text(text)
    assert compacted.splitlines()[0].endswith(" …")
    assert len(compacted.splitlines()[0]) < 500


def test_filter_saves_full_log_and_reports(tmp_path: Path) -> None:
    out = io.StringIO()
    run_filter(io.StringIO(noisy_output()), out, log_dir=tmp_path)
    text = out.getvalue()
    assert "[jev-claude] saída compactada: 301 -> " in text
    (log,) = tmp_path.glob("*.log")
    assert str(log) in text
    assert log.read_text(encoding="utf-8") == noisy_output()


def test_filter_passes_short_output_without_footer(tmp_path: Path) -> None:
    out = io.StringIO()
    run_filter(io.StringIO("tudo certo\n"), out, log_dir=tmp_path)
    assert out.getvalue() == "tudo certo\n"
    assert list(tmp_path.iterdir()) == []


def test_old_logs_are_pruned(tmp_path: Path) -> None:
    for i in range(25):
        (tmp_path / f"2000{i:04d}.log").write_text("x", encoding="utf-8")
    save_log("novo", tmp_path)
    assert len(list(tmp_path.glob("*.log"))) == 20


@pytest.mark.parametrize(
    "command",
    [
        "pytest",
        "pytest -x tests/test_core.py",
        "python -m pytest -q",
        "uv run pytest",
        "npm test",
        "npm run build",
        "pnpm run test:unit",
        "yarn install",
        "npx vitest run",
        "cargo test --all",
        "go test ./...",
        "./gradlew build",
        "make",
        "dotnet test",
        "ruff check .",
        "mypy",
        "CI=1 npm test",
    ],
)
def test_noisy_commands_are_wrapped(command: str) -> None:
    wrapped = wrap_command(command, python="/venv/bin/python")
    assert wrapped == f"/venv/bin/python -m jev_claude run -- {command}"


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "git status",
        "cat pytest.ini",
        "echo pytest",
        "pytest | tail -5",
        "pytest > out.txt",
        "cd app && npm test",
        "npm test; echo done",
        "pytest &",
        "pytest $(cat args)",
        "npm test 2>&1 | jev-claude compact",
        "npm run dev",
        "",
        "pytest 'aspas sem fim",
    ],
)
def test_other_commands_are_left_alone(command: str) -> None:
    assert wrap_command(command) is None


def test_windows_python_path_uses_forward_slashes() -> None:
    wrapped = wrap_command("pytest", python="C:\\Users\\Ana Lima\\venv\\Scripts\\python.exe")
    assert wrapped is not None and wrapped.endswith(" -m jev_claude run -- pytest")
    if sys.platform == "win32":
        assert wrapped.startswith("'C:/Users/Ana Lima/venv/Scripts/python.exe'")


def event(command: str, **extra: object) -> dict[str, object]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command, "description": "Roda os testes", "timeout": 120000, **extra},
    }


def test_hook_rewrites_and_keeps_other_fields_without_approving() -> None:
    answer = pre_bash_hook(event("pytest -q"), env={})
    assert answer is not None
    output = answer["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in output  # a permissão continua com o usuário
    updated = output["updatedInput"]
    assert updated["description"] == "Roda os testes" and updated["timeout"] == 120000
    assert updated["command"].endswith(" -m jev_claude run -- pytest -q")


@pytest.mark.parametrize(
    ("payload", "env"),
    [
        (event("pytest"), {"JEV_CLAUDE_COMPACT": "0"}),
        (event("pytest", run_in_background=True), {}),
        (event("git status"), {}),
        ({"tool_name": "Read", "tool_input": {"file_path": "x"}}, {}),
        ({"tool_name": "Bash", "tool_input": "quebrado"}, {}),
    ],
)
def test_hook_does_nothing_when_it_should_not(payload: dict[str, object], env: dict[str, str]) -> None:
    assert pre_bash_hook(payload, env=env) is None


def test_run_hook_never_fails_on_bad_input() -> None:
    for raw in ["", "não é json", "[1, 2]", json.dumps(event("ls"))]:
        out = io.StringIO()
        assert run_hook("pre-bash", io.StringIO(raw), out) == 0
        assert out.getvalue() == ""


def test_run_refuses_unknown_commands(tmp_path: Path) -> None:
    out = io.StringIO()
    assert run_command(["rm", "-rf", str(tmp_path)], out) == 2
    assert "só aceita comandos de teste" in out.getvalue()
    assert tmp_path.exists()


def test_run_reports_missing_program() -> None:
    out = io.StringIO()
    assert run_command(["dotnet", "test"], out, env={"PATH": ""}) == 127
    assert "não foi possível executar dotnet" in out.getvalue()


def test_run_keeps_exit_code_env_and_compacts(tmp_path: Path) -> None:
    """Roda um pytest de verdade com 300 testes e uma falha, pelo mesmo caminho do hook."""
    tests = tmp_path / "test_muitos.py"
    tests.write_text(
        "import os\nimport pytest\n\n"
        "@pytest.mark.parametrize('i', range(300))\n"
        "def test_item(i):\n"
        "    assert i != 150 or os.environ['MODO'] == 'outro', 'quebrou no 150'\n",
        encoding="utf-8",
    )
    path = os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")])
    env = {**os.environ, "PATH": path, "JEV_CLAUDE_HOME": str(tmp_path / "home")}
    shutil_python = "python" if sys.platform == "win32" else Path(sys.executable).name
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "jev_claude",
            "run",
            "--",
            "MODO=ci",
            shutil_python,
            "-m",
            "pytest",
            "-v",
            "-p",
            "no:cacheprovider",
            str(tests),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=tmp_path,
        check=False,
    )
    assert done.returncode == 1, done.stdout
    assert "quebrou no 150" in done.stdout
    assert "1 failed, 299 passed" in done.stdout
    assert "[jev-claude] saída compactada:" in done.stdout
    assert len(done.stdout.splitlines()) < 150


def test_cli_hook_end_to_end(tmp_path: Path) -> None:
    env = {**os.environ, "JEV_CLAUDE_HOME": str(tmp_path)}
    hook = subprocess.run(
        [sys.executable, "-m", "jev_claude", "hook", "pre-bash"],
        input=json.dumps(event("npm test")),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=True,
    )
    updated = json.loads(hook.stdout)["hookSpecificOutput"]["updatedInput"]
    assert updated["command"].endswith(" -m jev_claude run -- npm test")
    compact = subprocess.run(
        [sys.executable, "-m", "jev_claude", "compact"],
        input=noisy_output(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=True,
    )
    assert "[jev-claude] saída compactada: 301 ->" in compact.stdout
