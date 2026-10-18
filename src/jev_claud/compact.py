"""Compacta saídas longas de testes e builds antes que entrem no contexto do Claude.

Sem LLM e sem rede: regras fixas, rápidas e fiéis ao original. Mantém o começo, o fim
(onde ficam os resumos) e cada linha com cara de erro, com um pouco de contexto. A saída
completa fica salva em disco, e o rodapé diz onde, para o Claude abrir se precisar.

Usos:

- `comando 2>&1 | jev-claud compact`: filtro de texto.
- `jev-claud run -- pytest -x`: roda, compacta e sai com o código do comando.
- Hook `PreToolUse` do Claude Code (`jev-claud hook pre-bash`): reescreve só comandos
  simples e ruidosos (testes, builds, linters) para `jev-claud run`, tanto na ferramenta
  Bash quanto na PowerShell (a que o app desktop usa no Windows). A permissão continua
  sendo pedida normalmente: o hook não aprova nada.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

from jev_claud import savings
from jev_claud.envvars import getenv
from jev_claud.store import default_home

MIN_LINES = 80
HEAD_LINES = 5
TAIL_LINES = 25
CONTEXT_LINES = 2
MAX_IMPORTANT = 120
MAX_LINE_CHARS = 400
KEEP_LOGS = 20
# Ferramentas de shell do Claude Code que o hook atende (o `matcher` é uma regex).
SHELL_TOOLS = ("Bash", "PowerShell")
HOOK_MATCHER = "|".join(SHELL_TOOLS)
OFF_VALUES = frozenset({"0", "false", "no", "off", "nao", "não"})

_IMPORTANT = re.compile(
    r"error|fail|fatal|panic|exception|traceback|assert|warning|warn\b|denied|not found|"
    r"cannot|could not|unable|undefined|expected|missing|timeout|timed out|"
    r"^\s*E\s{2,}|^\s*>\s|^\s*File \"|^FAILED|^ERROR|✗|✖|×",
    re.IGNORECASE,
)

# Comandos que costumam despejar centenas de linhas. Casam com o início do comando.
_NOISY = re.compile(
    r"""^(
        (python3?|py)\s+-m\s+(pytest|unittest|tox|nox|mypy|pip\s+install)\b
      | (uv|poetry|pdm|hatch)\s+run\s+(pytest|tox|mypy|ruff)\b
      | (pytest|tox|nox|mypy)\b
      | ruff\s+check\b
      | pip3?\s+install\b
      | (npm|pnpm|yarn|bun)\s+(test|t|ci|install|i|run\s+(test|build|lint|typecheck|check)[\w:-]*)\b
      | (npm|pnpm|yarn|bun)\s+(build|lint)\b
      | npx\s+(jest|vitest|tsc|eslint|playwright\s+test)\b
      | (jest|vitest|tsc|eslint)\b
      | cargo\s+(build|test|check|clippy)\b
      | go\s+(build|test|vet)\b
      | (mvn|\./mvnw|gradle|\./gradlew)\b
      | make\b
      | dotnet\s+(build|test)\b
    )""",
    re.VERBOSE,
)
# Qualquer coisa que torne o comando composto: aí não mexemos.
_COMPOUND = re.compile(r"[|;<>`&\n]|\$\(")
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$")


def logs_dir() -> Path:
    return default_home() / "logs"


def enabled(env: Mapping[str, str] | None = None) -> bool:
    value = getenv("JEV_CLAUD_COMPACT", "1", env)
    return value.strip().lower() not in OFF_VALUES


def compact_text(text: str, *, min_lines: int = MIN_LINES) -> tuple[str, bool]:
    """Devolve (texto, compactou?). Saídas curtas passam intactas."""
    lines = text.splitlines()
    if len(lines) <= min_lines:
        return text, False
    keep: set[int] = set(range(min(HEAD_LINES, len(lines))))
    keep.update(range(max(0, len(lines) - TAIL_LINES), len(lines)))
    important = 0
    for index, line in enumerate(lines):
        if important >= MAX_IMPORTANT:
            break
        if index in keep or not _IMPORTANT.search(line):
            continue
        important += 1
        keep.update(range(max(0, index - CONTEXT_LINES), min(len(lines), index + CONTEXT_LINES + 1)))
    out: list[str] = []
    skipped = 0
    for index, line in enumerate(lines):
        if index not in keep:
            skipped += 1
            continue
        if skipped:
            out.append(f"… [{skipped} linhas omitidas]")
            skipped = 0
        out.append(line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + " …")
    return "\n".join(out), True


def save_log(text: str, directory: Path | None = None) -> Path | None:
    """Guarda a saída completa (mantém só as últimas `KEEP_LOGS`). Falha em silêncio."""
    directory = directory or logs_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{uuid.uuid4().hex[:8]}.log"
        path.write_text(text, encoding="utf-8")
        for old in sorted(directory.glob("*.log"))[:-KEEP_LOGS]:
            old.unlink(missing_ok=True)
    except OSError:
        return None
    return path


def run_filter(stdin: TextIO, stdout: TextIO, *, log_dir: Path | None = None) -> int:
    write_compacted(stdin.read(), stdout, log_dir=log_dir)
    return 0


def write_compacted(text: str, stdout: TextIO, *, log_dir: Path | None = None) -> None:
    compacted, changed = compact_text(text)
    if not changed:
        stdout.write(text)
        return
    path = save_log(text, log_dir)
    if path is None:
        # Não destruir a única cópia da evidência quando o disco falha.
        stdout.write(text)
        return
    savings.record(savings.COMPACT, len(text) - len(compacted))
    before, after = len(text.splitlines()), len(compacted.splitlines())
    where = f"; completa em {path}" if path else ""
    stdout.write(f"{compacted}\n[jev-claud] saída compactada: {before} -> {after} linhas{where}\n")
    stdout.write("[jev-claud] Investigue com diagnose_log(path=<log acima>, exit_code=<saída observada>).\n")


def _parse(words: list[str]) -> tuple[dict[str, str], list[str]] | None:
    """Separa `VAR=valor` iniciais do comando; None se não for um teste/build conhecido."""
    env: dict[str, str] = {}
    while words and _ENV_ASSIGN.match(words[0]):
        name, _, value = words[0].partition("=")
        env[name] = value
        words = words[1:]
    if not words or not _NOISY.match(" ".join(words)):
        return None
    return env, words


def _python_arg(python: str | None = None) -> str:
    """Python absoluto, citado para o shell: funciona mesmo sem o comando no PATH
    (no Git Bash do Windows, por exemplo, o atalho `jev-claud.cmd` não é encontrado)."""
    return shlex.quote(Path(python or sys.executable).as_posix())


def runner_prefix(python: str | None = None, shell: str = "Bash") -> str:
    if shell == "PowerShell":
        # Operador de chamada com o caminho entre aspas simples (' vira '' no PowerShell).
        quoted = Path(python or sys.executable).as_posix().replace("'", "''")
        return f"& '{quoted}' -m jev_claud run --"
    return f"{_python_arg(python)} -m jev_claud run --"


def hook_command(python: str | None = None) -> str:
    """Comando do hook `PreToolUse` que o `jev-claud setup` grava no Claude Code."""
    return f"{_python_arg(python)} -m jev_claud hook pre-bash"


def wrap_command(command: str, python: str | None = None, shell: str = "Bash") -> str | None:
    """Reescreve um comando simples e ruidoso para `jev-claud run`; senão, None.

    O resultado é um comando simples (sem pipe nem subshell), que o Claude Code consegue
    analisar e casar com as regras de permissão, e que sai com o código do comando original.
    `shell` é a ferramenta do Claude Code que vai rodá-lo: `Bash` ou `PowerShell`.
    """
    stripped = command.strip()
    if not stripped or "jev-claud" in stripped or "jev_claud" in stripped or _COMPOUND.search(stripped):
        return None
    try:
        words = shlex.split(stripped)
    except ValueError:
        return None
    parsed = _parse(words)
    if parsed is None or (shell == "PowerShell" and parsed[0]):
        return None  # `VAR=valor cmd` não é sintaxe do PowerShell: deixa o comando como está
    return f"{runner_prefix(python, shell)} {stripped}"


def run_command(
    argv: Sequence[str], stdout: TextIO, *, log_dir: Path | None = None, env: Mapping[str, str] | None = None
) -> int:
    """`jev-claud run -- comando`: executa, compacta a saída e devolve o código do comando.

    Só aceita os testes, builds e linters conhecidos: assim, permitir `jev_claud run` nas
    regras do Claude Code não vira uma porta para rodar qualquer coisa.
    """
    parsed = _parse(list(argv))
    if parsed is None:
        stdout.write("jev-claud run: só aceita comandos de teste, build ou lint (ex.: pytest, npm test).\n")
        return 2
    extra, words = parsed
    full_env = {**(env if env is not None else os.environ), **extra}
    # `which` respeita o PATHEXT no Windows: acha `npm.cmd`, `gradlew.bat` etc.
    executable = shutil.which(words[0], path=full_env.get("PATH")) or words[0]
    try:
        done = subprocess.run(
            [executable, *words[1:]],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=full_env,
            check=False,
        )
    except OSError as error:
        stdout.write(f"jev-claud run: não foi possível executar {words[0]}: {error}\n")
        return 127
    except KeyboardInterrupt:
        return 130
    output = done.stdout.decode("utf-8", errors="replace")
    if done.returncode != 0 and len(output.splitlines()) <= MIN_LINES:
        stdout.write(output)
        path = save_log(output, log_dir)
        if path is not None:
            stdout.write(f"\n[jev-claud] comando falhou: exit_code={done.returncode}; completa em {path}\n")
            stdout.write("[jev-claud] Use diagnose_log para organizar as evidências dessa falha.\n")
    else:
        write_compacted(output, stdout, log_dir=log_dir)
    return done.returncode


def pre_bash_hook(event: Mapping[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """Resposta do hook `PreToolUse` (Bash ou PowerShell), ou None para deixar o comando como está."""
    shell = event.get("tool_name")
    if not enabled(env) or shell not in SHELL_TOOLS:
        return None
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict) or tool_input.get("run_in_background"):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str):
        return None
    wrapped = wrap_command(command, shell=str(shell))
    if wrapped is None:
        return None
    # Sem `permissionDecision`: o Claude Code continua pedindo permissão como sempre.
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": {**tool_input, "command": wrapped},
        }
    }


def run_hook(name: str, stdin: TextIO, stdout: TextIO) -> int:
    """Ponto de entrada dos hooks. Nunca bloqueia o Claude: em caso de erro, não faz nada."""
    if name != "pre-bash":
        return 0
    try:
        event = json.loads(stdin.read() or "{}")
    except ValueError:
        return 0
    if not isinstance(event, dict):
        return 0
    answer = pre_bash_hook(event)
    if answer is not None:
        stdout.write(json.dumps(answer, ensure_ascii=False))
    return 0
