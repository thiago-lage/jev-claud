"""Linha de comando do jev-claude.

Subcomandos: setup, mcp, compact, run, hook, doctor, ask, write, models, spend, update, cache.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any, TextIO

from jev_claude import __version__
from jev_claude.core import JevClaude
from jev_claude.decision import Region
from jev_claude.errors import JevClaudeError
from jev_claude.media import Image
from jev_claude.models import forget_newest, resolve_model
from jev_claude.questions import Question, box, choice, noul, score
from jev_claude.wizard import PACKAGE_SPEC, Mode, needs_first_run, run_setup


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if getattr(args, "standalone", None):
        result: int = args.standalone(args)
        return result
    if not hasattr(args, "handler"):
        if argv is None and needs_first_run():
            # Como o `claude` na primeira vez: abre o assistente em vez da ajuda.
            return run_setup("interactive", version=__version__)
        _splash()
        return 0
    try:
        with JevClaude() as r:
            result = args.handler(r, args)
            return result
    except JevClaudeError as error:
        print(f"erro: {error}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jev-claude",
        description="Decisões tipadas para o código (Jev) e texto para pessoas (LLM). Custo zero por padrão.",
    )
    parser.add_argument("--version", action="version", version=f"jev-claude {__version__}")
    sub = parser.add_subparsers()

    setup = sub.add_parser("setup", help="assistente que instala e configura tudo (Ollama, Claude Code, Jev)")
    setup_mode = setup.add_mutually_exclusive_group()
    setup_mode.add_argument("-y", "--yes", action="store_true", help="aceita tudo que não exige digitar nada")
    setup_mode.add_argument(
        "--check", action="store_true", help="só verifica; sai com 1 se faltar algo essencial"
    )
    setup.add_argument("--model", help="modelo do Ollama para texto (padrão: llama3.2)")
    setup.set_defaults(standalone=_setup)

    mcp = sub.add_parser("mcp", help="servidor MCP (stdio) para o Claude delegar decisões e textos")
    mcp.set_defaults(standalone=_mcp)

    compact = sub.add_parser(
        "compact", help="compacta a saída longa de um teste/build lida da entrada (sem LLM, grátis)"
    )
    compact.set_defaults(standalone=_compact)

    run = sub.add_parser(
        "run", help="roda um teste/build e compacta a saída, mantendo o código de saída (ex.: run -- pytest)"
    )
    run.add_argument("command", nargs=argparse.REMAINDER, help="o comando, depois de --")
    run.set_defaults(standalone=_run)

    hook = sub.add_parser("hook", help="hooks do plugin do Claude Code (lê o evento JSON da entrada)")
    hook.add_argument("name", choices=["pre-bash"])
    hook.set_defaults(standalone=_hook)

    doctor = sub.add_parser("doctor", help="mostra backends ativos, orçamento e cache")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(handler=_doctor)

    ask = sub.add_parser("ask", help="faz uma pergunta tipada sobre um texto e/ou uma imagem")
    ask.add_argument("state", nargs="?", default="", help="texto a avaliar (opcional com --image)")
    kind = ask.add_mutually_exclusive_group(required=True)
    kind.add_argument("--noul", metavar="PERGUNTA", help="condição sim/não")
    kind.add_argument("--choice", metavar="PERGUNTA", help="escolha entre --option")
    kind.add_argument("--score", metavar="PERGUNTA", help="grau ao longo de --level")
    kind.add_argument("--box", metavar="PERGUNTA", help="onde algo está na imagem (exige o Jev)")
    ask.add_argument("--image", type=Path, help="imagem a avaliar (PNG/JPEG; exige o Jev)")
    ask.add_argument("-o", "--option", action="append", default=[], help="opção (rótulo ou rótulo=descrição)")
    ask.add_argument("-l", "--level", action="append", default=[], help="nível do score, do menor ao maior")
    ask.add_argument("--model", help="latest | preview | newest | jev-X.Y.Z")
    ask.add_argument("--json", action="store_true")
    ask.set_defaults(handler=_ask)

    write = sub.add_parser("write", help="gera texto para uma pessoa ler (LLM)")
    write.add_argument("prompt")
    write.add_argument("--system")
    write.set_defaults(handler=_write)

    models = sub.add_parser("models", help="lista os modelos Jev da conta")
    models.set_defaults(handler=_models)

    spend = sub.add_parser("spend", help="gastos mensais registrados com o Jev")
    spend.set_defaults(handler=_spend)

    update = sub.add_parser(
        "update", help="atualiza o jev-claude, o SDK do Jev e redescobre o modelo mais novo"
    )
    update.add_argument("--dry-run", action="store_true")
    update.set_defaults(handler=_update)

    cache = sub.add_parser("cache", help="gerencia o cache local de decisões")
    cache.add_argument("action", choices=["clear", "size"])
    cache.set_defaults(handler=_cache)
    return parser


def _doctor(r: JevClaude, args: argparse.Namespace) -> int:
    status = r.status()
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0
    jev, budget = status["jev"], status["budget"]
    writer = r.writer
    print(f"jev-claude {__version__}")
    print("\nDecisões (o código ramifica):")
    print(f"  typesafe-sdk : {jev['sdk_version'] or 'não instalado  ->  pip install jev-claude[jev]'}")
    print(f"  TYPESAFE_API_KEY : {'definida' if jev['api_key'] else 'ausente'}")
    print(f"  modelo       : {jev['model_spec']}  ->  {r.resolve_model() if r.jev else '(offline)'}")
    if jev["enabled"]:
        print(f"  backend      : Jev (teto US$ {budget['monthly_usd']:.2f}/mês)")
    elif r.jev is not None:
        print("  backend      : offline  (modo custo zero: defina JEV_CLAUDE_BUDGET_USD para liberar o Jev)")
    else:
        print("  backend      : offline  (grátis, heurística local)")
    print(
        f"  gasto no mês : US$ {budget['spent_usd_this_month']:.6f} "
        f"({budget['input_tokens_this_month']} tokens, {budget['calls_this_month']} chamadas)"
    )
    print("\nTexto (uma pessoa lê):")
    print(f"  LLM          : {_describe_writer(writer)}")
    if r.jev is not None:
        print(f"  estratégia   : {r.strategy}  (offline aceito com confiança >= {r.cascade_accept:.2f})")
    print(f"\nCache: {status['cache']['entries']} decisões em {status['cache']['path']}")
    _print_routing(r)
    _print_savings(r)
    return 0


def _describe_writer(writer: Any) -> str:
    if writer is None:
        return "nenhuma  (rode o Ollama ou defina JEV_CLAUDE_LLM_BASE_URL + JEV_CLAUDE_LLM_MODEL)"
    model = getattr(writer, "model", "")
    base = getattr(writer, "base_url", "")
    return f"{writer.name} {model} {base}".strip()


def _ask(r: JevClaude, args: argparse.Namespace) -> int:
    question: Question
    if args.noul:
        question = noul(args.noul)
    elif args.choice:
        if not args.option:
            raise JevClaudeError("--choice precisa de pelo menos um --option.")
        options: dict[str, str | None] = {}
        for raw in args.option:
            label, _, description = raw.partition("=")
            options[label.strip()] = description.strip() or None
        question = choice(args.choice, options)
    elif args.box:
        question = box(args.box)
    else:
        question = score(args.score, args.level)
    state: Any = args.state
    if args.image is not None:
        image = Image(args.image)
        state = {"texto": args.state, "imagem": image} if args.state else image
    elif not args.state:
        raise JevClaudeError("Informe um texto ou --image.")
    decision = r.ask(state, question, model=args.model)
    if args.json:
        print(json.dumps(decision.as_dict(), ensure_ascii=False, indent=2))
    elif isinstance(decision.value, Region):
        sides = ", ".join(f"{k}={v:.3f}" for k, v in decision.value.as_dict().items())
        print(f"{sides}  confiança={decision.confidence:.2f} [{decision.band.value}]  via {decision.source}")
    else:
        detail = f" (p={decision.probability:.3f})" if decision.probability is not None else ""
        confidence = f"confiança={decision.confidence:.2f} [{decision.band.value}]"
        print(f"{decision.value}{detail}  {confidence}  via {decision.source}")
    return 0


def _write(r: JevClaude, args: argparse.Namespace) -> int:
    print(r.write(args.prompt, system=args.system))
    return 0


def _models(r: JevClaude, args: argparse.Namespace) -> int:
    if r.jev is None:
        raise JevClaudeError("Jev indisponível: defina TYPESAFE_API_KEY e instale jev-claude[jev].")
    for info in r.jev.list_models():
        print(f"{info.name:<16} {info.release_date}  {info.description}")
    return 0


def _spend(r: JevClaude, args: argparse.Namespace) -> int:
    rows = r.store.ledger()
    if not rows:
        print("Nenhum gasto registrado. Custo zero até aqui.")
    for month, usd, tokens, calls in rows:
        print(f"{month}  US$ {usd:.6f}  {tokens} tokens  {calls} chamadas")
    _print_routing(r)
    _print_savings(r)
    return 0


COMMANDS = (
    ("setup", "instala e configura tudo (Ollama, Claude Code, plugin)"),
    ("doctor", "o que está ativo, orçamento e cache"),
    ("spend", "gastos e economia do mês"),
    ('ask "texto" --noul "pergunta?"', "decisão tipada pela linha de comando"),
    ('write "instrução"', "texto com a LLM local"),
    ("update", "atualiza o jev-claude e o SDK do Jev"),
)


def _splash() -> None:
    """`jev-claude` sem argumentos: o mascote, os comandos principais e a economia do mês."""
    from jev_claude.wizard import UI, savings_footer

    ui = UI()
    ui.banner(__version__, title="jev-claude", footer=savings_footer())
    width = max(len(name) for name, _ in COMMANDS)
    for name, description in COMMANDS:
        ui.line(f"  {ui.paint(f'jev-claude {name:<{width}}', '1')}  {ui.paint(description, '2')}")
    ui.line(ui.paint("\n  Todos os comandos: jev-claude -h", "2"))


def _print_savings(r: JevClaude) -> None:
    from jev_claude.savings import summary
    from jev_claude.wizard import UI

    ui = UI()
    ui.line(ui.paint(f"\n{summary(r.store)}", "2"))


ROUTING_LABELS = {
    "cache": "cache (repetidas)",
    "offline": "offline (grátis)",
    "jev": "Jev (pagas)",
    "fallback": "offline por falta de orçamento/erro",
}


def _print_routing(r: JevClaude) -> None:
    routing = r.store.routing()
    total = sum(n for n, _ in routing.values())
    if not total:
        return
    print(f"\nQuem respondeu as perguntas este mês (estratégia: {r.strategy}):")
    for layer, label in ROUTING_LABELS.items():
        n, _ = routing.get(layer, (0, 0))
        if n:
            print(f"  {label:<38} {n:>6}  ({n / total:.0%})")
    saved = sum(tokens for _, tokens in routing.values())
    if saved:
        usd = r.budget.cost(saved)
        print(f"  economia estimada: ~{saved} tokens do Jev (~US$ {usd:.6f})")


def _setup(args: argparse.Namespace) -> int:
    mode: Mode = "yes" if args.yes else "check" if args.check else "interactive"
    return run_setup(mode, ollama_model=args.model, version=__version__)


def _mcp(args: argparse.Namespace) -> int:
    from jev_claude.mcp import serve

    return serve()


def _utf8_streams() -> tuple[TextIO, TextIO]:
    """Entrada e saída em UTF-8 mesmo no console do Windows (logs trazem qualquer caractere)."""
    for stream in (sys.stdin, sys.stdout):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    return sys.stdin, sys.stdout


def _compact(args: argparse.Namespace) -> int:
    from jev_claude.compact import run_filter

    return run_filter(*_utf8_streams())


def _run(args: argparse.Namespace) -> int:
    from jev_claude.compact import run_command

    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    return run_command(command, _utf8_streams()[1])


def _hook(args: argparse.Namespace) -> int:
    from jev_claude.compact import run_hook

    return run_hook(args.name, *_utf8_streams())


def _installed_editable() -> bool:
    """Instalação de desenvolvimento (`pip install -e`): não sobrescrever com a versão do GitHub."""
    try:
        raw = distribution("jev-claude").read_text("direct_url.json")
    except PackageNotFoundError:
        return True
    info = json.loads(raw) if raw else {}
    return bool(info.get("dir_info", {}).get("editable"))


def _running_windows_launcher() -> bool:
    """`jev-claude.exe` em execução: o Windows trava o arquivo e o pip não consegue substituí-lo."""
    return sys.platform == "win32" and Path(sys.argv[0]).stem.lower() == "jev-claude"


def _update(r: JevClaude, args: argparse.Namespace) -> int:
    commands = [[sys.executable, "-m", "pip", "install", "--upgrade", "typesafe-sdk"]]
    self_upgrade = not _installed_editable()
    if self_upgrade and _running_windows_launcher():
        print(
            "No Windows o jev-claude.exe não consegue se substituir enquanto roda. Atualize com:\n"
            f'  "{sys.executable}" -m jev_claude update\n'
            "(o comando `jev-claude` criado pelo instalador já usa essa forma)",
            file=sys.stderr,
        )
        return 1
    if self_upgrade:
        commands.append([sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE_SPEC])
    for command in commands:
        print("$ " + " ".join(command))
        if not args.dry_run:
            completed = subprocess.run(command, check=False)
            if completed.returncode != 0:
                return completed.returncode
    forget_newest(r.store)
    if r.jev is not None:
        newest = resolve_model("newest", backend=r.jev, store=r.store)
        print(f"Modelo Jev mais novo disponível: {newest}")
    print("Dica: JEV_CLAUDE_MODEL=latest segue a release estável automaticamente.")
    return 0


def _cache(r: JevClaude, args: argparse.Namespace) -> int:
    if args.action == "clear":
        print(f"{r.store.clear_cache()} decisões removidas do cache.")
    else:
        print(r.store.cache_size())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
