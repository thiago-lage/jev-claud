"""Confere e restaura os itens protegidos do repositório.

A lista fica em `.github/protected-files.txt`. Sem argumentos, mostra o que falta e de qual
commit cada item volta (código de saída 1 se faltar algo). Com `--aplicar`, restaura a partir
do histórico do git, sem fazer commit: revise com `git status` e faça o commit você mesmo.

    python tools/restaurar_protegidos.py
    python tools/restaurar_protegidos.py --aplicar
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

MANIFESTO = ".github/protected-files.txt"
RAIZ = Path(__file__).resolve().parent.parent


def _git(raiz: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=raiz, capture_output=True, text=True, check=False)


def ler_manifesto(raiz: Path) -> list[str]:
    """Caminhos do manifesto, sem comentários nem linhas vazias."""
    texto = (raiz / MANIFESTO).read_text(encoding="utf-8")
    itens = (linha.strip() for linha in texto.splitlines())
    return [item for item in itens if item and not item.startswith("#")]


def faltando(raiz: Path, itens: list[str]) -> list[str]:
    return [item for item in itens if not (raiz / item).exists()]


def origem(raiz: Path, caminho: str) -> str | None:
    """Revisão de onde o item volta: HEAD, se só sumiu da árvore de trabalho; senão, o pai do
    commit que o apagou. None se o git não conhece o arquivo."""
    if _git(raiz, "cat-file", "-e", f"HEAD:{caminho}").returncode == 0:
        return "HEAD"
    apagou = _git(raiz, "log", "-1", "--diff-filter=D", "--format=%H", "--", caminho).stdout.strip()
    return f"{apagou}^" if apagou else None


def restaurar(raiz: Path, caminho: str, revisao: str) -> bool:
    return _git(raiz, "checkout", revisao, "--", caminho).returncode == 0


def main(argv: list[str] | None = None, raiz: Path = RAIZ) -> int:
    parser = argparse.ArgumentParser(description="Confere e restaura os itens protegidos.")
    parser.add_argument("--aplicar", action="store_true", help="restaura o que falta pelo git")
    args = parser.parse_args(argv)

    # Sem o manifesto não há lista: ele é o primeiro a voltar.
    if not (raiz / MANIFESTO).exists():
        revisao = origem(raiz, MANIFESTO)
        if not (args.aplicar and revisao and restaurar(raiz, MANIFESTO, revisao)):
            print(f"x {MANIFESTO} sumiu (volta de: {revisao or 'desconhecido'})")
            if not args.aplicar:
                print("  Rode com --aplicar para restaurar.")
            return 1
        print(f"+ {MANIFESTO} restaurado de {revisao}")

    ausentes = faltando(raiz, ler_manifesto(raiz))
    if not ausentes:
        print("Todos os itens protegidos estão no lugar.")
        return 0

    falhas = 0
    for caminho in ausentes:
        revisao = origem(raiz, caminho)
        if revisao is None:
            print(f"x {caminho}: não encontrado no histórico do git")
            falhas += 1
        elif not args.aplicar:
            print(f"- {caminho}: sumiu (volta de {revisao})")
            falhas += 1
        elif restaurar(raiz, caminho, revisao):
            print(f"+ {caminho} restaurado de {revisao}")
        else:
            print(f"x {caminho}: falha ao restaurar de {revisao}")
            falhas += 1

    if not args.aplicar and falhas:
        print("Rode com --aplicar para restaurar, revise com `git status` e faça o commit.")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
