"""Linha de comando: `jev-claude setup | doctor | ask | write | models | spend | update | cache`."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, distribution
from typing import Any

from jev_claude import __version__
from jev_claude.core import JevClaude
from jev_claude.errors import JevClaudeError
from jev_claude.models import forget_newest, resolve_model
from jev_claude.questions import Question, choice, noul, score
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
        parser.print_help()
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

    doctor = sub.add_parser("doctor", help="mostra backends ativos, orçamento e cache")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(handler=_doctor)

    ask = sub.add_parser("ask", help="faz uma pergunta tipada sobre um texto")
    ask.add_argument("state", help="texto a avaliar")
    kind = ask.add_mutually_exclusive_group(required=True)
    kind.add_argument("--noul", metavar="PERGUNTA", help="condição sim/não")
    kind.add_argument("--choice", metavar="PERGUNTA", help="escolha entre --option")
    kind.add_argument("--score", metavar="PERGUNTA", help="grau ao longo de --level")
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
    print(f"\nCache: {status['cache']['entries']} decisões em {status['cache']['path']}")
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
    else:
        question = score(args.score, args.level)
    decision = r.ask(args.state, question, model=args.model)
    if args.json:
        print(json.dumps(decision.as_dict(), ensure_ascii=False, indent=2))
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
        return 0
    for month, usd, tokens, calls in rows:
        print(f"{month}  US$ {usd:.6f}  {tokens} tokens  {calls} chamadas")
    return 0


def _setup(args: argparse.Namespace) -> int:
    mode: Mode = "yes" if args.yes else "check" if args.check else "interactive"
    return run_setup(mode, ollama_model=args.model, version=__version__)


def _installed_editable() -> bool:
    """Instalação de desenvolvimento (`pip install -e`): não sobrescrever com a versão do GitHub."""
    try:
        raw = distribution("jev-claude").read_text("direct_url.json")
    except PackageNotFoundError:
        return True
    info = json.loads(raw) if raw else {}
    return bool(info.get("dir_info", {}).get("editable"))


def _update(r: JevClaude, args: argparse.Namespace) -> int:
    commands = [[sys.executable, "-m", "pip", "install", "--upgrade", "typesafe-sdk"]]
    if not _installed_editable():
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
