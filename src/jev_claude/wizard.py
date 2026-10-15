"""`jev-claude setup`: assistente no terminal que deixa o fluxo pronto sozinho.

Detecta o que falta, explica para que serve e pergunta antes de instalar cada peça:

1. Python 3.10+                       (informativo)
2. SDK do Jev (`typesafe-sdk`)        decisões tipadas pagas, quando houver chave
3. Ollama + servidor + modelo         texto grátis na sua máquina
4. Chave e orçamento do Jev           opcional; guardados em `config.json`
5. Claude Code + skill TypeSafe       o agente que usa tudo isso
6. Servidor MCP `jev-claude`          o Claude delega lotes ao Jev/Ollama e economiza tokens
7. Plugin `jev-claude` no Claude Code  saídas compactadas, skill e subagente explorador
8. Regra global no `~/.claude/CLAUDE.md` (atualizada quando muda de versão)

Modos: interativo (padrão), `--yes` (aceita tudo que não exige digitar nada) e `--check`
(só verifica; código de saída 1 se faltar algo essencial).
"""

from __future__ import annotations

import getpass
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TextIO

from jev_claude import mascot
from jev_claude.config import config_path, load_config, save_config
from jev_claude.writers import DEFAULT_OLLAMA_MODEL, ollama_host

REPO_URL = "https://github.com/thiago-lage/jev-claude"
PACKAGE_SPEC = f"jev-claude[jev] @ git+{REPO_URL}"
TYPESAFE_CONSOLE = "https://console.typesafe.ai/keys"
PLUGIN_REPO = "thiago-lage/jev-claude"
PLUGIN_MARKETPLACE = "jev-claude"
PLUGIN_ID = f"jev-claude@{PLUGIN_MARKETPLACE}"
RULE_START = "<!-- jev-claude:start -->"
RULE_END = "<!-- jev-claude:end -->"
RULE_VERSION = 2
CLAUDE_RULE = f"""{RULE_START}
<!-- versão {RULE_VERSION}: o `jev-claude setup` substitui este bloco inteiro ao atualizar -->
## Como trabalhar

- Pedido de mudança em código: **implemente direto**. Edite os arquivos, rode os testes e
  mostre o resultado. Não devolva o código no chat para a pessoa colar, a não ser que ela peça.
- Nunca delegue escrita ou edição de código ao Ollama/Jev: a qualidade da entrega é sua.
  As ferramentas `jev-claude` só servem para poupar contexto (ver abaixo).

## Quando o código que você escreve usa IA: Jev vs. LLM

- Saída de IA que o **código consome para ramificar** (if/switch, roteamento, filtros, ranking,
  validação): use **TypeSafe Jev** (`choice`, `noul`, `score`) via `jev-claude`
  (`from jev_claude import JevClaude`). Não peça JSON a uma LLM para depois fazer parse.
- Saída que **uma pessoa vai ler**: use **LLM** (`JevClaude.write`, Ollama local por padrão).
- Regras conhecidas, cálculos e buscas exatas ficam em código puro.
- Custo zero por padrão: o Jev é pago; só com orçamento explícito (`jev-claude setup`).
- Leia https://docs.typesafe.ai/llms.txt e use a skill `typesafe:typesafe-ai` ao integrar.

## Poupar contexto

- Arquivo de dados grande (CSV, logs, listas) para classificar, ranquear ou resumir: ferramentas
  MCP `classify`, `rank`, `summarize_file` com o CAMINHO do arquivo, em vez de lê-lo.
- Código-fonte que você vai editar: leia você mesmo (resumo não serve para editar).
- Saídas longas de testes e builds já chegam compactadas pelo hook do plugin; a saída completa
  fica no caminho indicado no rodapé, se precisar.
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

    def run(self, command: Sequence[str]) -> int:
        """Executa mostrando a saída no terminal do usuário."""
        try:
            return subprocess.run(list(command), check=False).returncode
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

    def ollama_models(self) -> list[str] | None:
        try:
            with urllib.request.urlopen(f"{ollama_host()}/api/tags", timeout=1) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError):
            return None
        return [str(m.get("name", "")) for m in data.get("models") or [] if isinstance(m, dict)]

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
        self, version: str, *, title: str = "Bem-vindo ao jev-claude setup!", footer: str | None = None
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
        ollama_model: str | None = None,
        claude_md: Path | None = None,
        version: str = "",
    ) -> None:
        self.sys = system or System()
        self.ui = ui or UI()
        stored = load_config().get("ollama_model")
        self.model = ollama_model or (str(stored) if stored else None) or DEFAULT_OLLAMA_MODEL
        self.claude_md = claude_md or self.sys.home / ".claude" / "CLAUDE.md"
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
            save_config({"setup_version": self.version or "dev", "ollama_model": self._model_to_store()})
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
            self.ui.note("pulado no modo --yes (precisa de você): rode `jev-claude setup`.")
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
                '  jev-claude ask "Fui cobrado duas vezes" --choice "Setor?" -o financeiro -o suporte'
            )
            self.ui.note('  jev-claude write "Diga olá ao cliente em uma frase"')
            if self.sys.which("claude") or self.sys.exists(self._claude_path()):
                self.ui.note("  claude      (abra o Claude Code em qualquer projeto)")
        else:
            missing = ", ".join(report.essential_missing)
            self.ui.line(self.ui.paint(f" Ainda falta: {missing}", "33"))
            hint = "jev-claude setup" if mode == "check" else "jev-claude setup  (quando quiser)"
            self.ui.note(f"Rode de novo: {hint}")
        self.ui.note(f"Configuração: {config_path()}")

    def _model_to_store(self) -> str | None:
        return None if self.model == DEFAULT_OLLAMA_MODEL else self.model

    # -- lista de passos -----------------------------------------------------------

    def steps(self) -> list[Step]:
        global_python = not self.sys.in_venv
        return [
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
                "ollama",
                "Ollama (LLM local e grátis para texto)",
                self._check_ollama,
                self._install_ollama,
                self._ollama_question(),
            ),
            Step(
                "ollama_server",
                "Servidor do Ollama",
                self._check_server,
                self._start_server,
                "Iniciar o Ollama agora?",
                requires=("ollama",),
            ),
            Step(
                "ollama_model",
                f"Modelo de texto ({self.model})",
                self._check_model,
                self._pull_model,
                f"Baixar o modelo {self.model} (~2 GB, uma vez só)?",
                requires=("ollama_server",),
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
                "Registrar o jev-claude como servidor MCP do Claude Code?",
                requires=("claude",),
                essential=False,
            ),
            Step(
                "plugin",
                "Plugin jev-claude no Claude Code",
                self._check_plugin,
                self._install_plugin,
                "Instalar o plugin (saídas de teste compactadas, skill e subagente explorador)?",
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

    # -- Ollama --------------------------------------------------------------------

    def _ollama_path(self) -> str | None:
        found = self.sys.which("ollama")
        if found:
            return found
        candidates: list[Path] = []
        if self.sys.platform == "win32":
            local = self.sys.env.get("LOCALAPPDATA")
            if local:
                candidates.append(Path(local) / "Programs" / "Ollama" / "ollama.exe")
        elif self.sys.platform == "darwin":
            candidates += [
                Path("/opt/homebrew/bin/ollama"),
                Path("/usr/local/bin/ollama"),
                Path("/Applications/Ollama.app/Contents/Resources/ollama"),
            ]
        else:
            candidates += [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")]
        return next((str(path) for path in candidates if self.sys.exists(path)), None)

    def _ollama_question(self) -> str:
        if self.sys.platform == "win32":
            return "Instalar o Ollama via winget? (aceita os termos do pacote; licença MIT)"
        if self.sys.platform == "darwin":
            return "Instalar o Ollama via Homebrew?"
        return "Instalar o Ollama com o script oficial (ollama.com/install.sh, pede sudo)?"

    def _check_ollama(self) -> tuple[bool, str]:
        path = self._ollama_path()
        return (True, path) if path else (False, "texto grátis precisa de uma LLM local")

    def _install_ollama(self) -> bool:
        if self.sys.platform == "win32":
            if not self.sys.which("winget"):
                self.ui.warn("winget não encontrado. Baixe em https://ollama.com/download")
                return False
            command = [
                "winget", "install", "-e", "--id", "Ollama.Ollama", "--silent",
                "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity",
            ]  # fmt: skip
        elif self.sys.platform == "darwin":
            if not self.sys.which("brew"):
                self.ui.warn("Homebrew não encontrado. Baixe em https://ollama.com/download")
                return False
            command = ["brew", "install", "ollama"]
        else:
            command = ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"]
        return self.sys.run(command) == 0

    def _check_server(self) -> tuple[bool, str]:
        models = self.sys.ollama_models()
        return (True, ollama_host()) if models is not None else (False, "não está rodando")

    def _start_server(self) -> bool:
        path = self._ollama_path()
        if path is None:
            return False
        app = Path(path).parent / "ollama app.exe"
        if self.sys.platform == "win32" and self.sys.exists(app):
            self.sys.spawn([str(app)])
        elif self.sys.platform == "darwin" and self.sys.exists(Path("/Applications/Ollama.app")):
            self.sys.spawn(["open", "-a", "Ollama"])
        else:
            self.sys.spawn([path, "serve"])
        for _ in range(30):
            if self.sys.ollama_models() is not None:
                return True
            self.sys.sleep(1)
        self.ui.warn("O servidor não respondeu em 30 s.")
        return False

    def _check_model(self) -> tuple[bool, str]:
        models = self.sys.ollama_models() or []
        wanted = self.model if ":" in self.model else f"{self.model}:latest"
        if wanted in models or self.model in models:
            return True, "baixado"
        return False, "não baixado"

    def _pull_model(self) -> bool:
        path = self._ollama_path()
        return path is not None and self.sys.run([path, "pull", self.model]) == 0

    # -- chave do Jev ----------------------------------------------------------------

    def _check_key(self) -> tuple[bool, str]:
        config = load_config()
        if self.sys.env.get("TYPESAFE_API_KEY") or config.get("typesafe_api_key"):
            raw = str(self.sys.env.get("JEV_CLAUDE_BUDGET_USD") or config.get("budget_usd") or 0)
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
        code, _ = self.sys.capture([path, "mcp", "get", "jev-claude"])
        return (
            (True, "ferramentas decide, classify, rank, summarize_file, write")
            if code == 0
            else (
                False,
                "o Claude ainda não consegue delegar trabalho ao Jev e ao Ollama",
            )
        )

    def _install_mcp(self) -> bool:
        path = self._claude()
        if not path:
            return False
        command = [
            path,
            "mcp",
            "add",
            "-s",
            "user",
            "jev-claude",
            "--",
            self.sys.python,
            "-m",
            "jev_claude",
            "mcp",
        ]
        ok = self.sys.run(command) == 0
        if ok:
            self.ui.note("Vale para as próximas sessões do Claude Code (abra uma nova).")
        return ok

    def _bash_shim(self) -> Path:
        return self.sys.home / ".local" / "bin" / "jev-claude"

    def _check_plugin(self) -> tuple[bool, str]:
        path = self._claude()
        if not path:
            return False, "precisa do Claude Code"
        code, output = self.sys.capture([path, "plugin", "list"])
        if code != 0 or PLUGIN_ID not in output:
            return False, "não instalado"
        if self.sys.platform == "win32" and not self.sys.exists(self._bash_shim()):
            return False, "falta o atalho do jev-claude para o Git Bash"
        return True, PLUGIN_ID

    def _install_plugin(self) -> bool:
        path = self._claude()
        if not path:
            return False
        if self.sys.platform == "win32":
            self._write_bash_shim()
        _, output = self.sys.capture([path, "plugin", "list"])
        if PLUGIN_ID in output:
            return True
        _, marketplaces = self.sys.capture([path, "plugin", "marketplace", "list"])
        added = PLUGIN_MARKETPLACE in marketplaces
        if not added and self.sys.run([path, "plugin", "marketplace", "add", PLUGIN_REPO]) != 0:
            return False
        ok = self.sys.run([path, "plugin", "install", PLUGIN_ID]) == 0
        if ok:
            self.ui.note("Vale para as próximas sessões do Claude Code (abra uma nova).")
        return ok

    def _write_bash_shim(self) -> None:
        """No Windows, o Claude Code roda comandos no Git Bash, que não acha `jev-claude.cmd`."""
        shim = self._bash_shim()
        python = Path(self.sys.python).as_posix()
        shim.parent.mkdir(parents=True, exist_ok=True)
        shim.write_bytes(f'#!/bin/sh\nexec "{python}" -m jev_claude "$@"\n'.encode())

    # -- regra global ------------------------------------------------------------------

    def _check_rule(self) -> tuple[bool, str]:
        if self.sys.exists(self.claude_md):
            text = self.claude_md.read_text(encoding="utf-8")
            if CLAUDE_RULE.strip() in text:
                return True, str(self.claude_md)
            if RULE_START in text:
                return False, "versão antiga (não pede para implementar direto)"
        return False, "Claude não sabe quando usar Jev ou LLM"

    def _write_rule(self) -> bool:
        """Acrescenta a regra ou troca o bloco antigo entre os marcadores, preservando o resto."""
        self.claude_md.parent.mkdir(parents=True, exist_ok=True)
        current = self.claude_md.read_text(encoding="utf-8") if self.claude_md.exists() else ""
        start = current.find(RULE_START)
        end = current.find(RULE_END, start)
        if start != -1 and end != -1:
            tail = current[end + len(RULE_END) :].removeprefix("\n")
            self.claude_md.write_text(current[:start] + CLAUDE_RULE + tail, encoding="utf-8")
            return True
        separator = "\n" if current and not current.endswith("\n") else ""
        spacer = "\n" if current else ""
        self.claude_md.write_text(current + separator + spacer + CLAUDE_RULE, encoding="utf-8")
        return True


def savings_footer() -> str | None:
    """Linha de economia do mês para o rodapé do banner; None se não der para ler o banco."""
    from jev_claude.savings import summary
    from jev_claude.store import Store

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


def run_setup(mode: Mode = "interactive", *, ollama_model: str | None = None, version: str = "") -> int:
    report = Setup(ollama_model=ollama_model, version=version).run(mode)
    return 0 if report.ok else 1


def needs_first_run() -> bool:
    """Primeira execução em um terminal interativo: ainda não houve setup."""
    return _isatty(sys.stdin) and _isatty(sys.stdout) and "setup_version" not in load_config()
