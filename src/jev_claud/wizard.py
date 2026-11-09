"""`jev-claud setup`: assistente no terminal que deixa o fluxo pronto sozinho.

Detecta o que falta, explica para que serve e pergunta antes de instalar cada peça:

1. Python 3.10+                       (informativo)
2. SDK do Jev (`typesafe-sdk`)        decisões tipadas pagas, quando houver chave
3. Chave e orçamento do Jev           opcional; guardados em `config.json`
4. Claude Code + skill TypeSafe       o agente que usa tudo isso
5. Servidor MCP `jev-claud`           ferramentas que operam no LLM da sessão e economizam tokens
6. Hook no `~/.claude/settings.json`  saídas longas de testes e builds compactadas
7. Plugin Jevin no Claude Code        skill e subagente explorador
8. Regra global no `~/.claude/CLAUDE.md` (atualizada quando muda de versão)

Não há modelo local para instalar: o texto fica com o LLM da sessão em que o jev-claud roda.

Modos: interativo (padrão), `--yes` (aceita tudo que não exige digitar nada) e `--check`
(só verifica; código de saída 1 se faltar algo essencial).
"""

from __future__ import annotations

import contextlib
import getpass
import importlib.util
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TextIO

from jev_claud import mascot
from jev_claud.compact import HOOK_MATCHER, hook_command
from jev_claud.config import CONFIG_KEYS, config_path, load_config, save_config
from jev_claud.envvars import getenv

REPO_URL = "https://github.com/thiago-lage/jev-claud"
PACKAGE_SPEC = "jev-claud[jev]"  # versão lançada no PyPI
DEV_PACKAGE_SPEC = f"jev-claud[jev] @ git+{REPO_URL}"  # `jev-claud update --dev`
TYPESAFE_CONSOLE = "https://console.typesafe.ai/keys"
PLUGIN_REPO = "thiago-lage/jev-claud"
PLUGIN_MARKETPLACE = "jev-claud"
PLUGIN_ID = f"jevin@{PLUGIN_MARKETPLACE}"
# Adicionado à conta do claude.ai (Personalização › Plugins) e sincronizado no Claude Code.
# Com ele ativo, a cópia do marketplace jev-claud impede o carregamento: fica só um dos dois.
PLUGIN_SYNCED_ID = "jevin@synced"
MCP_NAME = "jev-claud"
MCP_ARGS = ("-m", "jev_claud", "mcp")
# O mesmo servidor, trazido pelo plugin Jevin (`plugin/.mcp.json`, comando `jev-claud mcp`).
# É o que leva as ferramentas ao Cowork; no Claude Code, ele dispensa o registro do setup.
PLUGIN_MCP_NAME = f"plugin:jevin:{MCP_NAME}"
RULE_START = "<!-- jev-claud:start -->"
RULE_END = "<!-- jev-claud:end -->"
# Nomes até a 0.6 (o projeto se chamava jev-claude): o setup migra cada um deles.
OLD_MARKETPLACE = "jev-claude"
OLD_PLUGIN_IDS = (f"jev-claude@{OLD_MARKETPLACE}", f"jev@{OLD_MARKETPLACE}")
OLD_MCP_NAME = "jev-claude"
OLD_RULE_MARKERS = ("<!-- jev-claude:start -->", "<!-- jev-claude:end -->")
RULE_VERSION = 7
# O hook ou um MCP grava o Python de outra instalação: o `refresh` os traz para a atual.
ELSEWHERE = "aponta para outra instalação do jev-claud"
CLAUDE_RULE = f"""{RULE_START}
<!-- versão {RULE_VERSION}: o `jev-claud setup` e o `jev-claud update` substituem este bloco inteiro -->
## Como trabalhar

- Pedido de mudança em código: **implemente direto**. Edite os arquivos, rode os testes e
  mostre o resultado. Não devolva o código no chat para a pessoa colar, a não ser que ela peça.
- Nunca delegue escrita ou edição de código às ferramentas do jev-claud: a qualidade da entrega
  é sua. O agente principal planeja, interpreta evidências, edita e valida.

## Quando o código que você escreve usa IA: Jev vs. LLM

- Saída de IA que o **código consome para ramificar** (if/switch, roteamento, filtros, ranking,
  validação): use **TypeSafe Jev** (`choice`, `noul`, `score`) via `jev-claud`
  (`from jev_claud import JevClaud`). Não peça JSON a uma LLM para depois fazer parse.
- Saída que **uma pessoa vai ler**: use **LLM** (`JevClaud.write` com o `Writer` do app).
- Regras conhecidas, cálculos e buscas exatas ficam em código puro.
- Custo zero por padrão: o Jev é pago; só com orçamento explícito (`jev-claud setup`).
- Ao integrar, leia https://docs.typesafe.ai/llms.txt (no Claude Code, use também a skill
  `typesafe:typesafe-ai`).

## Evidências e contexto para o agente

- Falha de teste/build: use `diagnose_log` com o caminho do log completo e o `exit_code`
  observado. Ele organiza sinais, não determina a causa raiz. Leia o código e corrija você.
- `search_file` busca texto literal; `read_evidence` amplia linhas. Passe `fingerprint` ao
  conferir um diagnóstico anterior. Confira `source.truncated` e grupos omitidos.
- `needs_agent_review` e `abstained` devolvem a investigação ao agente principal.
  Não trate `candidate` como decisão. Confiança offline é lexical, não probabilidade de acerto.

- Arquivo de dados grande (CSV, logs, listas) para classificar, ranquear ou resumir: ferramentas
  MCP `classify`, `rank`, `summarize_file` com o CAMINHO do arquivo, em vez de lê-lo.
- `summarize_file` e `write` usam o LLM desta sessão: por MCP sampling quando o host oferece;
  senão devolvem `mode: "agent"` com os blocos ou a tarefa para você fazer (não há modelo local).
  O resumo é `verification=unverified`: confira afirmações importantes no original com
  `search_file`/`read_evidence`; prefira diagnóstico determinístico para logs.
- Passe `effort`, com os mesmos níveis do seletor do Claude: `baixo` (triagem), `medio` (padrão),
  `alto` ou `extra` (os detalhes importam), `max` e `ultracode` (`max` mais revisão contra os
  parciais). Ele controla só o tamanho dos blocos e a profundidade, nunca o modelo.
- Na sessão em nuvem as ferramentas MCP não existem: leia com `offset`/`limit`.
- Código-fonte que você vai editar: leia você mesmo (resumo não serve para editar).
- Saída de teste ou build com o rodapé `[jev-claud] saída compactada`: a saída completa está no
  caminho indicado ali, se precisar.
{RULE_END}
"""

Mode = Literal["interactive", "yes", "check"]


# ---------------------------------------------------------------------------
# Sistema (tudo que toca o mundo real fica aqui, para os testes substituírem)
# ---------------------------------------------------------------------------


class System:
    def __init__(self) -> None:
        self.platform = sys.platform
        self.home = Path.home()
        self.env: Mapping[str, str] = os.environ
        self.python = sys.executable
        self.in_venv = sys.prefix != sys.base_prefix

    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def exists(self, path: Path) -> bool:
        return path.exists()

    def run(self, command: Sequence[str], cwd: Path | None = None) -> int:
        """Executa mostrando a saída no terminal do usuário."""
        try:
            return subprocess.run(list(command), check=False, cwd=cwd).returncode
        except OSError as error:
            print(f"  não foi possível executar {command[0]}: {error}", file=sys.stderr)
            return 127

    def capture(self, command: Sequence[str]) -> tuple[int, str]:
        try:
            done = subprocess.run(
                list(command), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return 127, str(error)
        return done.returncode, done.stdout + done.stderr

    def spawn(self, command: Sequence[str]) -> None:
        """Inicia um processo em segundo plano que sobrevive ao assistente."""
        kwargs: dict[str, Any] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":  # checagem estática: as constantes só existem no Windows
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(list(command), **kwargs)

    def process_alive(self, pid: int) -> bool:
        """O processo `pid` ainda está rodando? Na dúvida, responde que sim (nada é apagado)."""
        if pid <= 0:
            return False
        if sys.platform == "win32":  # os.kill no Windows encerraria o processo: consulta pela API
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return bool(kernel32.GetLastError() == 5)  # acesso negado: existe, de outro usuário
            try:
                code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return True
                return code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True

    def remove_tree(self, path: Path) -> None:
        shutil.rmtree(path, ignore_errors=True)

    def sdk_installed(self) -> bool:
        return importlib.util.find_spec("typesafe_sdk") is not None

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


# ---------------------------------------------------------------------------
# Interface no terminal, no estilo do Claude Code
# ---------------------------------------------------------------------------


class UI:
    _ACCENT = "38;5;173"  # laranja-terracota do Claude
    _MASCOT = "38;5;73"  # verde-azulado do Jev

    def __init__(
        self,
        out: TextIO | None = None,
        *,
        ask: Callable[[str], str] = input,
        secret: Callable[[str], str] = getpass.getpass,
        interactive: bool | None = None,
        color: bool | None = None,
    ) -> None:
        self.out = out or sys.stdout
        self._ask = ask
        self._secret = secret
        tty = _isatty(self.out)
        self.interactive = interactive if interactive is not None else (tty and _isatty(sys.stdin))
        self.color = color if color is not None else (tty and "NO_COLOR" not in os.environ and _enable_vt())
        fancy, plain = "✔✖○›✻╭╮╰╯│─", "+x->*++++|-"
        chars = fancy if _can_encode(self.out, fancy) else plain
        names = ("ok", "no", "skip", "arrow", "star", "tl", "tr", "bl", "br", "v", "h")
        self.sym = dict(zip(names, chars, strict=True))

    def paint(self, text: str, code: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.color else text

    def line(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    def banner(
        self, version: str, *, title: str = "Bem-vindo ao jev-claud setup!", footer: str | None = None
    ) -> None:
        """Caixa de boas-vindas com o mascote à esquerda; `footer` sai em cinza logo abaixo."""
        s = self.sym
        art = mascot.render(_can_encode(self.out, mascot.BLOCKS))
        text = [
            title,
            "Jev decide o que o código ramifica.",
            "LLM escreve o que a pessoa lê.",
            "Custo zero por padrão.",
        ]
        art_width = max(map(len, art))
        version_line = f"v{version} · {REPO_URL.removeprefix('https://')}"
        height = max(len(art), len(text))
        # Cada linha: (figura do mascote, texto). O texto fica sempre na mesma coluna.
        rows = [("", "")]
        rows += [(art[i] if i < len(art) else "", text[i] if i < len(text) else "") for i in range(height)]
        rows += [("", ""), ("", version_line)]
        width = max(len(words) for _, words in rows) + art_width + 7
        border = self.paint(s["v"], self._ACCENT)
        self.line(self.paint(s["tl"] + s["h"] * width + s["tr"], self._ACCENT))
        for figure, words in rows:
            pad = " " * (width - art_width - 5 - len(words))
            if words == title:
                styled = self.paint(words, "1")
            elif words == version_line:
                styled = self.paint(words, "2")
            else:
                styled = words
            figure_part = self.paint(figure.ljust(art_width), self._MASCOT) if figure else " " * art_width
            self.line(f"{border}  {figure_part}   {styled}{pad}{border}")
        self.line(self.paint(s["bl"] + s["h"] * width + s["br"], self._ACCENT))
        if footer:
            self.line(self.paint(f"  {footer}", "2"))
        self.line()

    def status(self, kind: Literal["ok", "no", "skip"], title: str, detail: str = "") -> None:
        color = {"ok": "32", "no": "31", "skip": "2"}[kind]
        suffix = self.paint(f"  {detail}", "2") if detail else ""
        self.line(f" {self.paint(self.sym[kind], color)} {title}{suffix}")

    def note(self, text: str) -> None:
        self.line(f"   {self.paint(text, '2')}")

    def warn(self, text: str) -> None:
        self.line(f"   {self.paint('!', '33')} {text}")

    def confirm(self, question: str, default: bool = True) -> bool:
        hint = "S/n" if default else "s/N"
        try:
            answer = (
                self._ask(f"   {self.paint(self.sym['arrow'], self._ACCENT)} {question} ({hint}) ")
                .strip()
                .lower()
            )
        except EOFError:
            return False
        if not answer:
            return default
        return answer in {"s", "sim", "y", "yes"}

    def prompt(self, question: str, default: str = "") -> str:
        hint = f" [{default}]" if default else ""
        try:
            answer = self._ask(f"   {self.paint(self.sym['arrow'], self._ACCENT)} {question}{hint}: ").strip()
        except EOFError:
            return default
        return answer or default

    def prompt_secret(self, question: str) -> str:
        try:
            return self._secret(f"   {self.paint(self.sym['arrow'], self._ACCENT)} {question}: ").strip()
        except EOFError:
            return ""


def _listed(output: str, name: str) -> bool:
    """`name` aparece como item (não só como parte de outro nome) na saída de uma listagem."""
    return any(name in line.replace("❯", " ").split() for line in output.splitlines())


def _plugin_entries(output: str, name: str) -> list[tuple[str, str]]:
    """(versão, escopo) de cada instalação de `name` na saída de `claude plugin list`."""
    entries: list[tuple[str, str]] = []
    inside = False
    for line in output.splitlines():
        words = line.replace("❯", " ").replace(">", " ").split()
        if not words:
            continue
        if "@" in words[0] and ":" not in words[0]:
            inside = words[0] == name
            if inside:
                entries.append(("", ""))
            continue
        if inside and len(words) >= 2 and words[0] in ("Version:", "Scope:"):
            version, scope = entries[-1]
            entries[-1] = (words[1], scope) if words[0] == "Version:" else (version, words[1])
    return entries


def _older(found: str, wanted: str) -> bool:
    """`found` é uma versão anterior a `wanted`? Versões que não são X.Y.Z nunca são "anteriores"."""

    def parse(text: str) -> tuple[int, ...] | None:
        parts = text.strip().lstrip("v").split(".")[:3]
        return tuple(int(p) for p in parts) if len(parts) == 3 and all(p.isdigit() for p in parts) else None

    old, new = parse(found), parse(wanted)
    return old is not None and new is not None and old < new


def _same_path(a: str, b: str) -> bool:
    """O mesmo arquivo? No Windows, sem diferenciar maiúsculas nem `/` e `\\`."""
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _python_beside(command: str) -> str | None:
    """Python do ambiente de um executável `jev-claud` (Scripts/ ou bin/ de um venv ou Python)."""
    folder = Path(command).parent
    if folder.name.lower() == "scripts":  # Windows: no venv fica junto; no Python do sistema, acima
        candidates = [folder / "python.exe", folder.parent / "python.exe"]
    else:
        candidates = [folder / "python", folder / "python3"]
    return next((str(path) for path in candidates if path.is_file()), None)


def _load_json_object(path: Path) -> dict[str, Any] | None:
    """Objeto JSON de um arquivo de configuração; {} se não existe, None se não dá para editar."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_atomic(path: Path, text: str) -> None:
    """Grava num arquivo temporário e troca de uma vez: nunca deixa a configuração pela metade."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".jev-claud.tmp")
    partial.write_text(text, encoding="utf-8")
    os.replace(partial, path)


def _toml_string(value: str) -> str:
    """String TOML básica (as regras de escape do JSON servem para os caracteres usados aqui)."""
    return json.dumps(value, ensure_ascii=False)


def _toml_table_span(text: str, name: str) -> tuple[int, int] | None:
    """Início e fim da tabela `[name]` (até o próximo cabeçalho), ou None se não existe."""
    first, _, rest = name.partition(".")
    quoted = rf'(?:{re.escape(rest)}|"{re.escape(rest)}")' if rest else ""
    header = re.compile(rf"^\[\s*{re.escape(first)}\s*\.\s*{quoted}\s*\][ \t]*(?:#.*)?$", re.M)
    found = header.search(text)
    if found is None:
        return None
    following = re.compile(r"^\s*\[", re.M).search(text, found.end())
    return found.start(), following.start() if following else len(text)


def _valid_toml(text: str) -> bool:
    """Confere o TOML antes de gravar (Python 3.11+; no 3.10 não há leitor na biblioteca padrão)."""
    if sys.version_info >= (3, 11):
        import tomllib

        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return False
    return True


def _pre_tool_hooks(settings: Mapping[str, Any]) -> list[tuple[Any, dict[str, Any]]]:
    """Pares (matcher, hook) do `PreToolUse` no settings.json, ignorando o que fugir do formato esperado."""
    hooks = settings.get("hooks")
    groups = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
    found: list[tuple[Any, dict[str, Any]]] = []
    for group in groups if isinstance(groups, list) else []:
        entries = group.get("hooks") if isinstance(group, dict) else None
        if isinstance(entries, list):
            found += [(group.get("matcher"), hook) for hook in entries if isinstance(hook, dict)]
    return found


def _is_our_hook(hook: Any) -> bool:
    """Hook pre-bash gravado por qualquer versão do jev-claud (ou do jev-claude, até a 0.6)."""
    command = hook.get("command") if isinstance(hook, dict) else None
    return isinstance(command, str) and command.rstrip().endswith(" hook pre-bash") and "jev" in command


def _isatty(stream: Any) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def _can_encode(stream: Any, text: str) -> bool:
    try:
        text.encode(getattr(stream, "encoding", None) or "ascii")
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def _enable_vt() -> bool:
    """Liga as cores ANSI no console do Windows (no-op nos outros sistemas)."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError):
        return False


# ---------------------------------------------------------------------------
# Passos
# ---------------------------------------------------------------------------


@dataclass
class Step:
    key: str
    title: str
    check: Callable[[], tuple[bool, str]]
    fix: Callable[[], bool] | None = None
    question: str = ""
    requires: tuple[str, ...] = ()
    essential: bool = True
    default_yes: bool = True
    auto: bool = True  # pode rodar no modo --yes (não exige digitar nada)


@dataclass
class Report:
    results: dict[str, bool] = field(default_factory=dict)
    essential_missing: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.essential_missing


class Setup:
    def __init__(
        self,
        *,
        system: System | None = None,
        ui: UI | None = None,
        claude_md: Path | None = None,
        version: str = "",
    ) -> None:
        self.sys = system or System()
        self.ui = ui or UI()
        self.claude_md = claude_md or self.sys.home / ".claude" / "CLAUDE.md"
        self.claude_settings = self.claude_md.parent / "settings.json"
        self.version = version

    # -- execução -------------------------------------------------------------

    def run(self, mode: Mode = "interactive") -> Report:
        if mode == "interactive" and not self.ui.interactive:
            self.ui.note("Terminal não interativo: só verificando. Use --yes para instalar sem perguntas.")
            mode = "check"
        self.ui.banner(self.version, footer=savings_footer())
        report = Report()
        for step in self.steps():
            report.results[step.key] = ok = self._run_step(step, mode, report.results)
            if not ok and step.essential:
                report.essential_missing.append(step.title)
        if mode != "check":
            save_config({"setup_version": self.version or "dev"})
        self._summary(report, mode)
        return report

    def _run_step(self, step: Step, mode: Mode, done: Mapping[str, bool]) -> bool:
        blocked = [key for key in step.requires if not done.get(key)]
        if blocked:
            self.ui.status("skip", step.title, "aguardando o passo anterior")
            return False
        ok, detail = step.check()
        if ok:
            self.ui.status("ok", step.title, detail)
            return True
        self.ui.status("no" if step.essential else "skip", step.title, detail)
        if mode == "check" or step.fix is None:
            return False
        if mode == "yes" and not step.auto:
            self.ui.note("pulado no modo --yes (precisa de você): rode `jev-claud setup`.")
            return False
        if mode == "interactive" and not self.ui.confirm(step.question, step.default_yes):
            self.ui.note("pulado.")
            return False
        if not step.fix():
            self.ui.status("no", step.title, "não concluído")
            return False
        ok, detail = step.check()
        self.ui.status("ok" if ok else "no", step.title, detail)
        return ok

    def _summary(self, report: Report, mode: Mode) -> None:
        self.ui.line()
        if report.ok:
            self.ui.line(self.ui.paint(f" {self.ui.sym['star']} Tudo pronto!", "1;32"))
            self.ui.note("Experimente:")
            self.ui.note(
                '  jev-claud ask "Fui cobrado duas vezes" --choice "Setor?" -o financeiro -o suporte'
            )
            self.ui.note("  jev-claud diagnose arquivo.log   (evidências de uma falha, sem LLM)")
            if self.sys.which("claude") or self.sys.exists(self._claude_path()):
                self.ui.note("  claude      (abra o Claude Code em qualquer projeto)")
        else:
            missing = ", ".join(report.essential_missing)
            self.ui.line(self.ui.paint(f" Ainda falta: {missing}", "33"))
            hint = "jev-claud setup" if mode == "check" else "jev-claud setup  (quando quiser)"
            self.ui.note(f"Rode de novo: {hint}")
        self.ui.note(f"Configuração: {config_path()}")

    # -- lista de passos -----------------------------------------------------------

    def steps(self) -> list[Step]:
        global_python = not self.sys.in_venv
        steps = [
            Step("python", "Python 3.10+", self._check_python),
            Step(
                "sdk",
                "SDK do Jev (typesafe-sdk)",
                self._check_sdk,
                self._install_sdk,
                (
                    "Instalar no Python GLOBAL? Pode conflitar com outros pacotes"
                    if global_python
                    else "Instalar o typesafe-sdk neste ambiente?"
                ),
                essential=False,
                default_yes=not global_python,
                auto=not global_python,
            ),
            Step(
                "jev_key",
                "Chave do Jev (opcional, pago por uso)",
                self._check_key,
                self._ask_key,
                "Configurar agora a chave do Jev?",
                essential=False,
                default_yes=False,
                auto=False,
            ),
            Step(
                "claude",
                "Claude Code",
                self._check_claude,
                self._install_claude,
                "Instalar o Claude Code (instalador oficial de claude.ai)?",
                essential=False,
            ),
            Step(
                "skill",
                "Skill TypeSafe no Claude Code",
                self._check_skill,
                self._install_skill,
                "Instalar a skill TypeSafe (typesafe-ai/skills)?",
                requires=("claude",),
                essential=False,
            ),
            Step(
                "mcp",
                "Servidor MCP no Claude Code (economiza tokens)",
                self._check_mcp,
                self._install_mcp,
                "Registrar o jev-claud como servidor MCP do Claude Code?",
                requires=("claude",),
                essential=False,
            ),
            Step(
                "hook",
                "Saídas de teste compactadas no Claude Code",
                self._check_hook,
                self._install_hook,
                "Compactar as saídas longas de testes e builds antes de entrarem no contexto?",
                requires=("claude",),
                essential=False,
            ),
            Step(
                "plugin",
                "Plugin Jevin no Claude Code",
                self._check_plugin,
                self._install_plugin,
                "Configurar o plugin Jevin (skill e subagente explorador)?",
                requires=("claude",),
                essential=False,
            ),
            Step(
                "rule",
                "Regra global no ~/.claude/CLAUDE.md",
                self._check_rule,
                self._write_rule,
                "Adicionar a regra 'código ramifica -> Jev; pessoa lê -> LLM'?",
                essential=False,
            ),
        ]
        # Outros agentes: só aparecem quando estão instalados neste computador.
        if self._has_cursor():
            steps.append(
                Step(
                    "cursor_mcp",
                    "Cursor: ferramentas MCP do jev-claud",
                    self._check_cursor_mcp,
                    self._install_cursor_mcp,
                    "Registrar o jev-claud como servidor MCP do Cursor?",
                    essential=False,
                )
            )
        if self._has_codex():
            steps += [
                Step(
                    "codex_mcp",
                    "Codex: ferramentas MCP do jev-claud",
                    self._check_codex_mcp,
                    self._install_codex_mcp,
                    "Registrar o jev-claud como servidor MCP do Codex?",
                    essential=False,
                ),
                Step(
                    "codex_rule",
                    "Codex: regra global no AGENTS.md",
                    self._check_codex_rule,
                    self._write_codex_rule,
                    "Adicionar a regra 'código ramifica -> Jev; pessoa lê -> LLM' ao Codex?",
                    essential=False,
                ),
            ]
        return steps

    # -- Python e SDK --------------------------------------------------------------

    def _check_python(self) -> tuple[bool, str]:
        version = ".".join(map(str, sys.version_info[:3]))
        where = "ambiente isolado" if self.sys.in_venv else "Python global"
        return sys.version_info >= (3, 10), f"{version} ({where})"

    def _check_sdk(self) -> tuple[bool, str]:
        if self.sys.sdk_installed():
            return True, "instalado"
        return False, "só é necessário com a chave do Jev"

    def _install_sdk(self) -> bool:
        if not self.sys.in_venv:
            self.ui.warn("Prefira o instalador oficial, que usa um ambiente isolado (veja o README).")
        return self.sys.run([self.sys.python, "-m", "pip", "install", "--upgrade", "typesafe-sdk"]) == 0

    # -- chave do Jev ----------------------------------------------------------------

    def _check_key(self) -> tuple[bool, str]:
        config = load_config()
        if self.sys.env.get("TYPESAFE_API_KEY") or config.get("typesafe_api_key"):
            raw = str(self.sys.env.get("JEV_CLAUD_BUDGET_USD") or config.get("budget_usd") or 0)
            try:
                budget = float(raw.replace(",", "."))
            except ValueError:
                budget = 0.0
            detail = "custo zero (orçamento US$ 0)" if budget <= 0 else f"teto de US$ {budget:.2f}/mês"
            return True, detail
        return False, "sem chave: decisões rodam no modo offline grátis"

    def _ask_key(self) -> bool:
        self.ui.note(f"Crie uma chave em {TYPESAFE_CONSOLE} (o Jev cobra US$ 0,042 por milhão de tokens).")
        key = self.ui.prompt_secret("Cole a chave (Enter para pular)")
        if not key:
            return False
        budget_raw = self.ui.prompt("Orçamento mensal em US$ (0 = não gastar nada)", "1")
        try:
            budget = max(0.0, float(budget_raw.replace(",", ".")))
        except ValueError:
            self.ui.warn("Valor inválido; usando US$ 0 (custo zero).")
            budget = 0.0
        path = save_config({"typesafe_api_key": key, "budget_usd": budget})
        self.ui.note(f"Guardado em {path} (só o seu usuário acessa).")
        return True

    # -- Claude Code -----------------------------------------------------------------

    def _claude_path(self) -> Path:
        name = "claude.exe" if self.sys.platform == "win32" else "claude"
        return self.sys.home / ".local" / "bin" / name

    def _claude(self) -> str | None:
        found = self.sys.which("claude")
        if found:
            return found
        path = self._claude_path()
        return str(path) if self.sys.exists(path) else None

    def _check_claude(self) -> tuple[bool, str]:
        path = self._claude()
        if not path:
            return False, "não instalado"
        code, output = self.sys.capture([path, "--version"])
        return True, output.strip().splitlines()[0] if code == 0 and output.strip() else path

    def _install_claude(self) -> bool:
        if self.sys.platform == "win32":
            command = [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-Command", "irm https://claude.ai/install.ps1 | iex",
            ]  # fmt: skip
        else:
            command = ["bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash"]
        ok = self.sys.run(command) == 0
        if ok:
            self.ui.note("Na primeira vez, rode `claude` para entrar na sua conta.")
        return ok

    def _check_skill(self) -> tuple[bool, str]:
        path = self._claude()
        if not path:
            return False, "precisa do Claude Code"
        code, output = self.sys.capture([path, "plugin", "list"])
        if code == 0 and "typesafe@typesafe-ai" in output:
            return True, "typesafe@typesafe-ai"
        return False, "não instalada"

    def _install_skill(self) -> bool:
        path = self._claude()
        if not path:
            return False
        _, marketplaces = self.sys.capture([path, "plugin", "marketplace", "list"])
        if "typesafe-ai" not in marketplaces:
            added = self.sys.run([path, "plugin", "marketplace", "add", "typesafe-ai/skills"])
            if added != 0:
                return False
        return self.sys.run([path, "plugin", "install", "typesafe@typesafe-ai"]) == 0

    def _check_mcp(self) -> tuple[bool, str]:
        path = self._claude()
        if not path:
            return False, "precisa do Claude Code"
        code, _ = self.sys.capture([path, "mcp", "get", MCP_NAME])
        plugin = self._plugin_mcp(path)
        if code == 0 and plugin:
            return False, f"duplicado: o plugin Jevin já traz o servidor ({PLUGIN_MCP_NAME})"
        if code == 0:
            return True, "ferramentas decide, classify, rank, summarize_file, write"
        if plugin:
            return True, f"pelo plugin Jevin ({PLUGIN_MCP_NAME})"
        old, _ = self.sys.capture([path, "mcp", "get", OLD_MCP_NAME])
        if old == 0:
            return False, f"registrado com o nome antigo ({OLD_MCP_NAME})"
        return False, "o Claude ainda não tem as ferramentas do jev-claud"

    def _plugin_mcp(self, path: str) -> bool:
        """O plugin Jevin trouxe o servidor MCP e ele conecta (o `jev-claud` está no PATH)."""
        code, output = self.sys.capture([path, "mcp", "get", PLUGIN_MCP_NAME])
        return code == 0 and "connected" in output.lower()

    def _drop_duplicate_mcp(self, path: str) -> None:
        """Com o servidor vindo do plugin, o registro do setup só repetiria as ferramentas no contexto."""
        code, _ = self.sys.capture([path, "mcp", "get", MCP_NAME])
        if code == 0 and self._plugin_mcp(path):
            self.sys.run([path, "mcp", "remove", "-s", "user", MCP_NAME])
            self.ui.status("ok", "Servidor MCP", "agora vem do plugin Jevin; registro repetido removido")

    def _install_mcp(self) -> bool:
        path = self._claude()
        if not path:
            return False
        old, _ = self.sys.capture([path, "mcp", "get", OLD_MCP_NAME])
        if old == 0:
            self.sys.run([path, "mcp", "remove", "-s", "user", OLD_MCP_NAME])
        if self._plugin_mcp(path):
            self._drop_duplicate_mcp(path)
            return True
        command = [
            path,
            "mcp",
            "add",
            "-s",
            "user",
            MCP_NAME,
            "--",
            self.sys.python,
            "-m",
            "jev_claud",
            "mcp",
        ]
        ok = self.sys.run(command) == 0
        if ok:
            self.ui.note("Vale para as próximas sessões do Claude Code (abra uma nova).")
        return ok

    def _bash_shim(self, name: str = "jev-claud") -> Path:
        return self.sys.home / ".local" / "bin" / name

    def _check_plugin(self) -> tuple[bool, str]:
        path = self._claude()
        if not path:
            return False, "precisa do Claude Code"
        code, output = self.sys.capture([path, "plugin", "list"])
        if code == 0 and _listed(output, PLUGIN_SYNCED_ID):
            if _listed(output, PLUGIN_ID):
                return False, f"duplicado: o {PLUGIN_SYNCED_ID} da sua conta só carrega sem o {PLUGIN_ID}"
            return True, f"{PLUGIN_SYNCED_ID} (sua conta do Claude)"
        if code != 0 or not _listed(output, PLUGIN_ID):
            old = next((name for name in OLD_PLUGIN_IDS if _listed(output, name)), None)
            if old is not None:
                return False, f"nome antigo ({old}): atualizar para {PLUGIN_ID}"
            return False, "não instalado"
        stale = self._stale_plugin_versions(output)
        if stale:
            return False, f"versão {stale[0]}: atualizar para {self.version}"
        return True, PLUGIN_ID

    def _stale_plugin_versions(self, output: str) -> list[str]:
        """Versões instaladas do Jevin anteriores a esta do jev-claud (skill e subagente velhos)."""
        return [v for v, _ in _plugin_entries(output, PLUGIN_ID) if self.version and _older(v, self.version)]

    def _update_plugin(self, path: str, output: str) -> bool:
        """Leva o Jevin do marketplace para a versão mais nova, em cada escopo instalado."""
        entries = _plugin_entries(output, PLUGIN_ID)
        if not self._stale_plugin_versions(output):
            return True
        if any(scope == "project" for _, scope in entries) and self._drop_missing_projects(path):
            _, output = self.sys.capture([path, "plugin", "list"])
            entries = _plugin_entries(output, PLUGIN_ID)
            if not self._stale_plugin_versions(output):
                return True
        self.sys.run([path, "plugin", "marketplace", "update", PLUGIN_MARKETPLACE])
        ok = True
        if any(scope in ("user", "") for _, scope in entries):
            ok = self.sys.run([path, "plugin", "update", "-s", "user", PLUGIN_ID]) == 0
        project_installs = sum(1 for _, scope in entries if scope == "project")
        if project_installs:
            projects = self._stale_plugin_projects(path)
            for project in projects:
                command = [path, "plugin", "update", "-s", "project", PLUGIN_ID]
                if self.sys.run(command, cwd=project) != 0:
                    ok = False
            if len(projects) < project_installs:
                self.ui.note(
                    "O Jevin também está instalado em projetos que não achei: em cada um deles, rode "
                    f"`claude plugin update -s project {PLUGIN_ID}`."
                )
        if ok:
            self.ui.note("Vale para as próximas sessões do Claude Code (abra uma nova).")
        return ok

    def _stale_plugin_projects(self, path: str) -> list[Path]:
        """Pastas dos projetos com o Jevin instalado numa versão anterior (`plugin list --json`)."""
        code, output = self.sys.capture([path, "plugin", "list", "--json"])
        try:
            data = json.loads(output) if code == 0 else []
        except ValueError:
            return []
        projects: list[Path] = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict) or item.get("id") != PLUGIN_ID or item.get("scope") != "project":
                continue
            folder = item.get("projectPath")
            version = str(item.get("version") or "")
            if isinstance(folder, str) and _older(version, self.version) and self.sys.exists(Path(folder)):
                projects.append(Path(folder))
        return projects

    def _plugin_cache(self) -> Path:
        return self.sys.home / ".claude" / "plugins" / "cache" / PLUGIN_MARKETPLACE / "jevin"

    def _clean_old_plugins(self, path: str) -> tuple[list[str], list[str]]:
        """Apaga as versões anteriores do Jevin que ficaram no cache do Claude Code.

        Só apaga o que o próprio Claude Code já marcou como órfão (`.orphaned_at`), que é anterior
        a esta versão, que nenhuma instalação usa e que nenhuma sessão aberta registrou em
        `.in_use`. Devolve (apagadas, mantidas porque ainda há sessão aberta usando).
        """
        root = self._plugin_cache()
        if not self.sys.exists(root):
            return [], []
        _, output = self.sys.capture([path, "plugin", "list"])
        installed = {version for version, _ in _plugin_entries(output, PLUGIN_ID)}
        removed: list[str] = []
        kept: list[str] = []
        for folder in sorted(root.iterdir()):
            name = folder.name
            if (
                not folder.is_dir()
                or name in installed
                or not _older(name, self.version)
                or not (folder / ".orphaned_at").exists()
            ):
                continue
            if self._held_by_open_session(folder):
                kept.append(name)
                continue
            self.sys.remove_tree(folder)
            removed.append(name)
        return removed, kept

    def _held_by_open_session(self, folder: Path) -> bool:
        marks = folder / ".in_use"
        if not marks.is_dir():
            return False
        for mark in marks.iterdir():
            if not mark.name.isdigit() or self.sys.process_alive(int(mark.name)):
                return True  # formato desconhecido ou sessão aberta: não arrisca
        return False

    def _legacy_install_dir(self) -> Path:
        """Venv do instalador até a 0.6, quando o projeto se chamava jev-claude."""
        return self.sys.home / ".local" / "share" / "jev-claude"

    def _legacy_data_dir(self) -> Path | None:
        """Dados da 0.6 (`jev-claude`), se a pasta nova também existe; `None` com JEV_CLAUD_HOME."""
        if getenv("JEV_CLAUD_HOME", env=self.sys.env).strip():
            return None
        if self.sys.platform == "win32" and self.sys.env.get("LOCALAPPDATA"):
            base = Path(self.sys.env["LOCALAPPDATA"])
        else:
            xdg = self.sys.env.get("XDG_CACHE_HOME", "").strip()
            base = Path(xdg) if xdg else self.sys.home / ".cache"
        old, new = base / "jev-claude", base / "jev-claud"
        return old if self.sys.exists(old) and self.sys.exists(new) else None

    def _referenced_anywhere(self, folder: Path) -> bool:
        """Alguma configuração (hook, MCP do Claude Code, Cursor, Codex) ainda aponta para `folder`?"""
        if Path(self.sys.python).resolve().is_relative_to(folder.resolve()):
            return True
        needles = {str(folder), folder.as_posix(), str(folder).replace("\\", "\\\\")}
        configs = (
            self.claude_settings,
            self.sys.home / ".claude.json",
            self.sys.home / ".cursor" / "mcp.json",
            self._codex_dir() / "config.toml",
        )
        for config in configs:
            try:
                text = config.read_text(encoding="utf-8")
            except OSError:
                continue
            if any(needle in text for needle in needles):
                return True
        return False

    def _clean_legacy_files(self) -> list[str]:
        """Apaga o que a 0.6 (jev-claude) deixou e nada mais usa. Devolve o que foi apagado."""
        removed: list[str] = []
        install = self._legacy_install_dir()
        if self.sys.exists(install) and not self._referenced_anywhere(install):
            self.sys.remove_tree(install)
            removed.append(str(install))
        data = self._legacy_data_dir()
        if data is not None and not self._referenced_anywhere(data):
            old_key = _load_json_object(data / "config.json") or {}
            new_key = _load_json_object(data.parent / "jev-claud" / "config.json") or {}
            if old_key.get("typesafe_api_key") and not new_key.get("typesafe_api_key"):
                self.ui.warn(f"Mantive {data}: ele tem uma chave do Jev que a pasta nova não tem.")
            else:
                self.sys.remove_tree(data)
                removed.append(str(data))
        return removed

    def _report_cleanup(self, path: str) -> None:
        removed, kept = self._clean_old_plugins(path)
        if removed:
            self.ui.status("ok", "Versões antigas do Jevin", "apagadas: " + ", ".join(removed))
        if kept:
            self.ui.note(
                f"{', '.join(kept)}: ainda em uso por uma sessão aberta; sai no próximo `jev-claud update`."
            )

    def _drop_missing_projects(self, path: str) -> int:
        """Tira o Jevin dos projetos cuja pasta foi apagada; devolve quantos registros saíram.

        O Claude Code só desinstala de dentro da pasta do projeto: ela é recriada vazia por um
        instante e apagada de novo. Um caminho num disco que não está montado fica como está.
        """
        code, output = self.sys.capture([path, "plugin", "list", "--json"])
        try:
            data = json.loads(output) if code == 0 else []
        except ValueError:
            return 0
        removed = 0
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict) or item.get("id") != PLUGIN_ID or item.get("scope") != "project":
                continue
            folder = item.get("projectPath")
            project = Path(folder) if isinstance(folder, str) and folder else None
            if project is None or self.sys.exists(project) or not self.sys.exists(Path(project.anchor)):
                continue
            created = [project, *(p for p in project.parents if not self.sys.exists(p))]
            try:
                project.mkdir(parents=True)
            except OSError:
                continue
            ok = self.sys.run([path, "plugin", "uninstall", "-s", "project", PLUGIN_ID], cwd=project) == 0
            for leftover in created:  # do mais fundo para cima, só o que continua vazio
                try:
                    leftover.rmdir()
                except OSError:
                    break
            if ok:
                removed += 1
                self.ui.status("ok", "Jevin em projeto apagado", f"registro removido: {project}")
        return removed

    def _shims(self) -> list[Path]:
        names = ("jev-claud", "jev-claud.cmd", "jev-claude", "jev-claude.cmd")
        return [self._bash_shim(name) for name in names]

    def _shim_targets(self, shim: Path) -> list[str]:
        """Pythons que um atalho de `~/.local/bin` chama (`"<python>" -m jev_claud`)."""
        text = shim.read_text(encoding="utf-8", errors="replace") if self.sys.exists(shim) else ""
        return re.findall(r'"([^"]+)"\s+-m\s+jev_claud\b', text)

    def _referenced_pythons(self, *, with_path: bool = True) -> list[str]:
        """Pythons de instalações do jev-claud que algo usa: hook, MCPs, atalhos e o comando no PATH."""
        found: list[str] = []
        for _, hook in _pre_tool_hooks(self._read_settings() or {}):
            if _is_our_hook(hook):
                with contextlib.suppress(ValueError, IndexError):
                    found.append(shlex.split(hook["command"])[0])
        for config in (self.sys.home / ".claude.json", self._cursor_dir() / "mcp.json"):
            servers = (_load_json_object(config) or {}).get("mcpServers")
            entry = servers.get(MCP_NAME) if isinstance(servers, dict) else None
            if isinstance(entry, dict) and isinstance(entry.get("command"), str):
                found.append(entry["command"])
        codex = self._codex_dir() / "config.toml"
        text = codex.read_text(encoding="utf-8") if self.sys.exists(codex) else ""
        span = _toml_table_span(text, f"mcp_servers.{MCP_NAME}")
        table = text[span[0] : span[1]] if span else ""
        match = re.search(r'^command\s*=\s*"((?:[^"\\]|\\.)*)"', table, re.M)
        if match:
            found.append(re.sub(r"\\(.)", r"\1", match.group(1)))
        for shim in self._shims():
            found += self._shim_targets(shim)
        command = self.sys.which("jev-claud") if with_path else None
        if command and not any(_same_path(command, str(shim)) for shim in self._shims()):
            python = _python_beside(command)
            if python:
                found.append(python)
        return found

    def _repoint_configs(self, path: str | None) -> None:
        """O que já usa o jev-claud passa a usar esta instalação, a que acabou de ser atualizada."""
        for title, check, fix in (
            ("Hook de compactação", self._check_hook, self._install_hook),
            ("Cursor: MCP do jev-claud", self._check_cursor_mcp, self._install_cursor_mcp),
            ("Codex: MCP do jev-claud", self._check_codex_mcp, self._install_codex_mcp),
        ):
            if check()[1] == ELSEWHERE and fix():
                self.ui.status("ok", title, "agora usa esta instalação")
        if not path:
            return
        code, output = self.sys.capture([path, "mcp", "get", MCP_NAME])
        match = re.search(r"^\s*Command:\s*(.+?)\s*$", output, re.M) if code == 0 else None
        if match and not _same_path(match.group(1), self.sys.python):
            self.sys.run([path, "mcp", "remove", "-s", "user", MCP_NAME])
            command = [path, "mcp", "add", "-s", "user", MCP_NAME, "--", self.sys.python, *MCP_ARGS]
            if self.sys.run(command) == 0:
                self.ui.status("ok", "Servidor MCP no Claude Code", "agora usa esta instalação")

    def _remove_unused_installs(self, pythons: Sequence[str]) -> None:
        """Desinstala o jev-claud dos outros Pythons que a configuração usava e não usa mais.

        Assim as cópias não ficam divergindo a cada versão. Só sai uma instalação comum (a
        editável é o clone de alguém), que nenhum hook, MCP ou atalho usa, e só quando o comando
        `jev-claud` continua existindo pelo atalho desta instalação.
        """
        in_use = self._referenced_pythons(with_path=False)
        shim = self._bash_shim("jev-claud.cmd" if self.sys.platform == "win32" else "jev-claud")
        shimmed = any(_same_path(target, self.sys.python) for target in self._shim_targets(shim))
        seen: list[str] = []
        for python in pythons:
            if (
                _same_path(python, self.sys.python)
                or any(_same_path(python, other) for other in [*in_use, *seen])
                or not self.sys.exists(Path(python))
            ):
                continue
            seen.append(python)
            code, output = self.sys.capture([python, "-m", "pip", "show", "jev-claud"])
            if code != 0 or "Editable project location" in output:
                continue
            if not shimmed:
                self.ui.note(f"Outra instalação do jev-claud: `{python} -m pip uninstall jev-claud`.")
                continue
            if self.sys.run([python, "-m", "pip", "uninstall", "-y", "-q", "jev-claud"]) == 0:
                self.ui.status("ok", "Instalação anterior do jev-claud", f"removida de {python}")

    def _prune_config(self) -> None:
        """Tira do config.json as chaves que esta versão não usa mais (o modelo local, por exemplo)."""
        path = config_path()
        stale = sorted(set(_load_json_object(path) or {}) - CONFIG_KEYS)
        if stale:
            save_config({}, path)  # regrava só com as chaves conhecidas
            self.ui.status("ok", "Configuração", "chaves que saíram de uso: " + ", ".join(stale))

    def _install_plugin(self) -> bool:
        path = self._claude()
        if not path:
            return False
        _, output = self.sys.capture([path, "plugin", "list"])
        _, marketplaces = self.sys.capture([path, "plugin", "marketplace", "list"])
        if _listed(output, PLUGIN_SYNCED_ID):
            # Veio da conta do Claude: a cópia do marketplace só atrapalharia.
            if _listed(output, PLUGIN_ID) and self.sys.run([path, "plugin", "uninstall", PLUGIN_ID]) != 0:
                return False
        elif not _listed(output, PLUGIN_ID):
            if _listed(marketplaces, PLUGIN_MARKETPLACE):
                self.sys.run([path, "plugin", "marketplace", "update", PLUGIN_MARKETPLACE])
            elif self.sys.run([path, "plugin", "marketplace", "add", PLUGIN_REPO]) != 0:
                return False
            if self.sys.run([path, "plugin", "install", PLUGIN_ID]) != 0:
                return False
            self.ui.note("Vale para as próximas sessões do Claude Code (abra uma nova).")
        elif not self._update_plugin(path, output):
            return False
        else:
            self._report_cleanup(path)
        for old in OLD_PLUGIN_IDS:
            if _listed(output, old):
                self.sys.run([path, "plugin", "uninstall", old])
        if _listed(marketplaces, OLD_MARKETPLACE):
            self.sys.run([path, "plugin", "marketplace", "remove", OLD_MARKETPLACE])
        self._drop_duplicate_mcp(path)
        return True

    def _write_bash_shim(self) -> None:
        """No Windows, o Claude Code roda comandos no Git Bash, que não acha arquivos `.cmd`.

        Cria `jev-claud` e o nome antigo `jev-claude`, os dois apontando para este Python.
        """
        python = Path(self.sys.python).as_posix()
        for name in ("jev-claud", "jev-claude"):
            shim = self._bash_shim(name)
            shim.parent.mkdir(parents=True, exist_ok=True)
            shim.write_bytes(f'#!/bin/sh\nexec "{python}" -m jev_claud "$@"\n'.encode())

    # -- hook de compactação -------------------------------------------------------------
    # Fica no settings.json do usuário, e não no plugin: o hook chama o Python do jev-claud,
    # que está fora do plugin (o diretório da Anthropic exigiria revisão manual do script).

    def _read_settings(self) -> dict[str, Any] | None:
        """settings.json do Claude Code; {} se não existe, None se não dá para editar com segurança."""
        return _load_json_object(self.claude_settings)

    def _check_hook(self) -> tuple[bool, str]:
        settings = self._read_settings()
        if settings is None:
            return False, f"{self.claude_settings} não é um JSON válido"
        ours = [
            (matcher, hook["command"]) for matcher, hook in _pre_tool_hooks(settings) if _is_our_hook(hook)
        ]
        commands = [command for _, command in ours]
        if hook_command(self.sys.python) not in commands:
            if commands:
                return False, ELSEWHERE
            return False, "testes e builds longos enchem o contexto do Claude"
        if (HOOK_MATCHER, hook_command(self.sys.python)) not in ours:
            return False, "versão antiga: não cobre o PowerShell (app desktop no Windows)"
        if self.sys.platform == "win32" and not self.sys.exists(self._bash_shim()):
            return False, "falta o atalho do jev-claud para o Git Bash"
        return True, "testes e builds chegam compactados (desligue com JEV_CLAUD_COMPACT=0)"

    def _install_hook(self) -> bool:
        """Grava o hook no settings.json, preservando o resto e trocando versões anteriores dele."""
        settings = self._read_settings()
        hooks = settings.get("hooks", {}) if settings is not None else None
        groups = hooks.get("PreToolUse", []) if isinstance(hooks, dict) else None
        if settings is None or not isinstance(hooks, dict) or not isinstance(groups, list):
            self.ui.warn(f"Não mexi em {self.claude_settings}: o conteúdo não está no formato esperado.")
            return False
        kept: list[Any] = []
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) else None
            if isinstance(entries, list):
                others = [hook for hook in entries if not _is_our_hook(hook)]
                if len(others) != len(entries):
                    if not others:
                        continue
                    group = {**group, "hooks": others}
            kept.append(group)
        entry = {"type": "command", "command": hook_command(self.sys.python), "timeout": 10}
        kept.append({"matcher": HOOK_MATCHER, "hooks": [entry]})
        settings["hooks"] = {**hooks, "PreToolUse": kept}
        if self.sys.platform == "win32":
            self._write_bash_shim()
        _write_atomic(self.claude_settings, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
        self.ui.note("Vale para as próximas sessões do Claude Code (abra uma nova).")
        return True

    # -- regra global ------------------------------------------------------------------

    def _check_rule(self) -> tuple[bool, str]:
        return self._rule_state(self.claude_md, "Claude não sabe quando usar Jev ou LLM")

    def _write_rule(self) -> bool:
        return self._write_rule_to(self.claude_md)

    def _rule_state(self, path: Path, missing: str) -> tuple[bool, str]:
        """A regra está em `path` na versão atual? (CLAUDE.md, AGENTS.md...)"""
        if self.sys.exists(path):
            text = path.read_text(encoding="utf-8")
            if CLAUDE_RULE.strip() in text:
                return True, str(path)
            if RULE_START in text or OLD_RULE_MARKERS[0] in text:
                return False, "versão antiga: atualizar"
        return False, missing

    def _rule_outdated(self, path: Path) -> bool:
        """A regra do jev-claud está em `path`, mas numa versão anterior."""
        if not self.sys.exists(path):
            return False
        text = path.read_text(encoding="utf-8")
        has_rule = RULE_START in text or OLD_RULE_MARKERS[0] in text
        return has_rule and CLAUDE_RULE.strip() not in text

    def refresh(self) -> int:
        """Depois do `jev-claud update`: leva o que já está instalado para esta versão.

        Só atualiza o que existe (regra no CLAUDE.md e no AGENTS.md do Codex, plugin Jevin);
        não instala nada novo. Roda no processo da versão nova, para usar a regra nova.
        No fim, limpa o que sobrou de versões anteriores: o Jevin de projetos apagados, as versões
        dele no cache que nenhuma sessão aberta usa, o registro MCP que o plugin passou a trazer,
        o marketplace `jev-claude`, as pastas da 0.6 (venv e dados) que nada usa, as chaves
        obsoletas do config.json e, depois de trazer hook e MCPs para esta instalação, o
        jev-claud dos outros Pythons que eles usavam.
        """
        failed = False
        previous = self._referenced_pythons()
        for rule, title in (
            (self.claude_md, "Regra global do Claude"),
            (self._codex_rule_path(), "Regra do Codex"),
        ):
            if self._rule_outdated(rule):
                self._write_rule_to(rule)
                self.ui.status("ok", title, f"atualizada para a versão {RULE_VERSION}")
        path = self._claude()
        if path:
            code, output = self.sys.capture([path, "plugin", "list"])
            if code == 0 and _listed(output, PLUGIN_ID):
                stale = self._stale_plugin_versions(output)
                if not stale:
                    self.ui.status("ok", "Plugin Jevin", "já está na versão atual")
                elif self._update_plugin(path, output):
                    self.ui.status("ok", "Plugin Jevin", f"{stale[0]} -> {self.version}")
                else:
                    failed = True
                    self.ui.status(
                        "no", "Plugin Jevin", f"não atualizou: rode `claude plugin update {PLUGIN_ID}`"
                    )
                if not failed:
                    self._report_cleanup(path)
            elif code == 0 and _listed(output, PLUGIN_SYNCED_ID):
                self.ui.status("ok", "Plugin Jevin", "vem da sua conta do Claude, que o atualiza")
            if not failed:
                self._drop_duplicate_mcp(path)
            _, marketplaces = self.sys.capture([path, "plugin", "marketplace", "list"])
            if _listed(marketplaces, OLD_MARKETPLACE):
                self.sys.run([path, "plugin", "marketplace", "remove", OLD_MARKETPLACE])
                self.ui.status("ok", "Marketplace antigo", f"{OLD_MARKETPLACE} removido")
        self._repoint_configs(path)
        self._remove_unused_installs(previous)
        for folder in self._clean_legacy_files():
            self.ui.status("ok", "Arquivos da 0.6 (jev-claude)", f"apagado: {folder}")
        self._prune_config()
        return 1 if failed else 0

    def _write_rule_to(self, path: Path) -> bool:
        """Acrescenta a regra ou troca o bloco antigo entre os marcadores, preservando o resto."""
        path.parent.mkdir(parents=True, exist_ok=True)
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        for start_marker, end_marker in ((RULE_START, RULE_END), OLD_RULE_MARKERS):
            start = current.find(start_marker)
            end = current.find(end_marker, start)
            if start != -1 and end != -1:
                break
        if start != -1 and end != -1:
            tail = current[end + len(end_marker) :].removeprefix("\n")
            path.write_text(current[:start] + CLAUDE_RULE + tail, encoding="utf-8")
            return True
        separator = "\n" if current and not current.endswith("\n") else ""
        spacer = "\n" if current else ""
        path.write_text(current + separator + spacer + CLAUDE_RULE, encoding="utf-8")
        return True

    # -- Cursor e Codex ------------------------------------------------------------------
    # As mesmas ferramentas MCP e a mesma regra, para quem também usa esses agentes. Os arquivos
    # de configuração são editados preservando o resto, como o settings.json do Claude Code.

    def _cursor_dir(self) -> Path:
        return self.sys.home / ".cursor"

    def _codex_dir(self) -> Path:
        custom = self.sys.env.get("CODEX_HOME", "").strip()
        return Path(custom).expanduser() if custom else self.sys.home / ".codex"

    def _has_cursor(self) -> bool:
        return bool(self.sys.which("cursor")) or self.sys.exists(self._cursor_dir())

    def _has_codex(self) -> bool:
        return bool(self.sys.which("codex")) or self.sys.exists(self._codex_dir())

    def _check_cursor_mcp(self) -> tuple[bool, str]:
        path = self._cursor_dir() / "mcp.json"
        data = _load_json_object(path)
        if data is None:
            return False, f"{path} não é um JSON válido"
        servers = data.get("mcpServers")
        entry = servers.get(MCP_NAME) if isinstance(servers, dict) else None
        if not isinstance(entry, dict):
            return False, "o Cursor ainda não usa as ferramentas do jev-claud"
        if entry.get("command") != self.sys.python or entry.get("args") != list(MCP_ARGS):
            return False, ELSEWHERE
        return True, "ferramentas decide, classify, rank, summarize_file, write"

    def _install_cursor_mcp(self) -> bool:
        path = self._cursor_dir() / "mcp.json"
        data = _load_json_object(path)
        servers = data.get("mcpServers", {}) if data is not None else None
        if data is None or not isinstance(servers, dict):
            self.ui.warn(f"Não mexi em {path}: o conteúdo não está no formato esperado.")
            return False
        current = servers.get(MCP_NAME)
        base = current if isinstance(current, dict) else {}
        entry = {**base, "type": "stdio", "command": self.sys.python, "args": list(MCP_ARGS)}
        data["mcpServers"] = {**servers, MCP_NAME: entry}
        _write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        self.ui.note("Reinicie o Cursor e confira em Settings › Tools & MCP se o jev-claud está ligado.")
        self.ui.note("A regra fica em Settings › Rules: `jev-claud rule` mostra o texto para colar.")
        return True

    def _codex_block(self) -> str:
        args = ", ".join(_toml_string(arg) for arg in MCP_ARGS)
        return f"[mcp_servers.{MCP_NAME}]\ncommand = {_toml_string(self.sys.python)}\nargs = [{args}]\n"

    def _check_codex_mcp(self) -> tuple[bool, str]:
        path = self._codex_dir() / "config.toml"
        text = path.read_text(encoding="utf-8") if self.sys.exists(path) else ""
        span = _toml_table_span(text, f"mcp_servers.{MCP_NAME}")
        if span is None:
            return False, "o Codex ainda não usa as ferramentas do jev-claud"
        if text[span[0] : span[1]].strip() != self._codex_block().strip():
            return False, ELSEWHERE
        return True, "ferramentas decide, classify, rank, summarize_file, write"

    def _install_codex_mcp(self) -> bool:
        path = self._codex_dir() / "config.toml"
        text = path.read_text(encoding="utf-8") if self.sys.exists(path) else ""
        span = _toml_table_span(text, f"mcp_servers.{MCP_NAME}")
        if span is not None:  # troca só a tabela do jev-claud; o que vem depois fica igual
            after = text[span[1] :].lstrip("\n")
            updated = text[: span[0]] + self._codex_block() + ("\n" + after if after else "")
        elif text.strip():  # no fim do arquivo, separada por uma linha em branco
            trailing = len(text) - len(text.rstrip("\n"))
            updated = text + "\n" * max(0, 2 - trailing) + self._codex_block()
        else:
            updated = self._codex_block()
        if not _valid_toml(updated):
            self.ui.warn(f"Não mexi em {path}: o conteúdo não está no formato esperado.")
            return False
        _write_atomic(path, updated)
        self.ui.note("Vale para as próximas sessões do Codex.")
        return True

    def _codex_rule_path(self) -> Path:
        """O Codex lê o AGENTS.override.md no lugar do AGENTS.md quando ele existe e não está vazio."""
        override = self._codex_dir() / "AGENTS.override.md"
        if self.sys.exists(override) and override.read_text(encoding="utf-8").strip():
            return override
        return self._codex_dir() / "AGENTS.md"

    def _check_codex_rule(self) -> tuple[bool, str]:
        return self._rule_state(self._codex_rule_path(), "o Codex não sabe quando usar Jev ou LLM")

    def _write_codex_rule(self) -> bool:
        return self._write_rule_to(self._codex_rule_path())


def savings_footer() -> str | None:
    """Linha de economia do mês para o rodapé do banner; None se não der para ler o banco."""
    from jev_claud.savings import summary
    from jev_claud.store import Store

    try:
        store = Store()
    except (OSError, sqlite3.Error):
        return None
    try:
        return summary(store)
    except sqlite3.Error:
        return None
    finally:
        store.close()


def run_setup(mode: Mode = "interactive", *, version: str = "") -> int:
    report = Setup(version=version).run(mode)
    return 0 if report.ok else 1


def run_refresh(*, version: str = "") -> int:
    return Setup(version=version).refresh()


def needs_first_run() -> bool:
    """Primeira execução em um terminal interativo: ainda não houve setup."""
    return _isatty(sys.stdin) and _isatty(sys.stdout) and "setup_version" not in load_config()
