"""Guarda dos itens protegidos e das garantias da cadeia de publicação.

Roda no CI, que é verificação obrigatória da `main`. Se algo aqui falhar porque um item sumiu,
recupere com `python tools/restaurar_protegidos.py --aplicar` (veja AGENTS.md).
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

RAIZ = Path(__file__).resolve().parent.parent
WORKFLOWS = RAIZ / ".github" / "workflows"
COMO_RECUPERAR = "Recupere com `python tools/restaurar_protegidos.py --aplicar` (veja AGENTS.md)."


def _restaurador() -> ModuleType:
    caminho = RAIZ / "tools" / "restaurar_protegidos.py"
    spec = importlib.util.spec_from_file_location("restaurar_protegidos", caminho)
    assert spec and spec.loader, f"tools/restaurar_protegidos.py sumiu. {COMO_RECUPERAR}"
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _workflows() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(WORKFLOWS.glob("*.yml"))}


# Itens protegidos ------------------------------------------------------------------------


def test_itens_protegidos_existem() -> None:
    restaurador = _restaurador()
    assert (RAIZ / restaurador.MANIFESTO).exists(), f"O manifesto sumiu. {COMO_RECUPERAR}"
    itens = restaurador.ler_manifesto(RAIZ)
    assert "AGENTS.md" in itens and restaurador.MANIFESTO in itens
    ausentes = restaurador.faltando(RAIZ, itens)
    assert not ausentes, f"Itens protegidos sumiram: {ausentes}. {COMO_RECUPERAR}"


def test_itens_protegidos_nao_estao_vazios() -> None:
    restaurador = _restaurador()
    vazios = [i for i in restaurador.ler_manifesto(RAIZ) if (RAIZ / i).stat().st_size == 0]
    assert not vazios, f"Itens protegidos esvaziados: {vazios}. {COMO_RECUPERAR}"


def test_normas_para_agentes_carregadas() -> None:
    claude_md = (RAIZ / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "@../AGENTS.md" in claude_md, "O CLAUDE.md do projeto deve importar o AGENTS.md."
    negados = " ".join(_settings_deny())
    for comando in ("git push --force", "git tag -d", "--no-verify", "gh release delete"):
        assert comando in negados, f".claude/settings.json deve bloquear `{comando}`."


def _settings_deny() -> list[str]:
    dados = json.loads((RAIZ / ".claude" / "settings.json").read_text(encoding="utf-8"))
    negados: list[str] = dados["permissions"]["deny"]
    return negados


# Workflows -------------------------------------------------------------------------------


def test_actions_fixadas_por_sha() -> None:
    soltas = []
    for nome, texto in _workflows().items():
        for ref in re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", texto, re.M):
            if ref.startswith(("./", "docker://")):
                continue
            if not re.search(r"@[0-9a-f]{40}$", ref):
                soltas.append(f"{nome}: {ref}")
    assert not soltas, f"Actions sem SHA fixo (troque a tag pelo commit): {soltas}"


def test_checkout_sem_credenciais_persistidas() -> None:
    faltando = []
    for nome, texto in _workflows().items():
        linhas = texto.splitlines()
        for i, linha in enumerate(linhas):
            if "actions/checkout@" in linha:
                trecho = "\n".join(linhas[i + 1 : i + 5])
                if "persist-credentials: false" not in trecho:
                    faltando.append(f"{nome}:{i + 1}")
    assert not faltando, f"checkout sem `persist-credentials: false`: {faltando}"


def test_workflows_com_permissoes_minimas() -> None:
    for nome, texto in _workflows().items():
        assert re.search(r"^permissions:\s*\n\s+contents: read", texto, re.M), (
            f"{nome}: precisa de `permissions: contents: read` no topo."
        )
        assert "pull_request_target" not in texto, f"{nome}: `pull_request_target` é proibido."
        assert "write-all" not in texto, f"{nome}: `write-all` é proibido."


def test_ci_roda_os_testes() -> None:
    ci = _workflows()["ci.yml"]
    assert "pytest" in ci and "ruff check" in ci and "mypy" in ci


def test_release_rascunho_atestado_e_ambiente_pypi() -> None:
    release = _workflows()["release.yml"]
    assert "--draft" in release, "O release deve nascer rascunho (releases imutáveis)."
    assert "--draft=false" in release, "O release deve ser publicado depois de anexar os arquivos."
    assert "attest-build-provenance" in release, "O wheel deve ganhar atestado de proveniência."
    assert re.search(r"environment:\s*\n\s+name: pypi", release), "O PyPI passa pelo ambiente pypi."
    assert "gh-action-pypi-publish" in release, "O PyPI é publicado só por trusted publishing."
    assert "password:" not in release and "PYPI_TOKEN" not in release, "Nada de token do PyPI."


# Restauração -----------------------------------------------------------------------------

requer_git = pytest.mark.skipif(shutil.which("git") is None, reason="git não instalado")


def _repo(tmp_path: Path) -> Path:
    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "protected-files.txt").write_text(
        "# teste\n.github/protected-files.txt\nLICENSE\n", encoding="utf-8"
    )
    (tmp_path / "LICENSE").write_text("MIT\n", encoding="utf-8")
    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "inicial")
    return tmp_path


@requer_git
def test_restaura_item_apagado_em_commit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raiz = _repo(tmp_path)
    autor = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "rm", "-q", "LICENSE"], cwd=raiz, check=True)
    subprocess.run(["git", *autor, "commit", "-q", "-m", "apaga"], cwd=raiz, check=True)
    restaurador = _restaurador()

    assert restaurador.main([], raiz=raiz) == 1
    assert "LICENSE: sumiu" in capsys.readouterr().out
    assert not (raiz / "LICENSE").exists()

    assert restaurador.main(["--aplicar"], raiz=raiz) == 0
    assert (raiz / "LICENSE").read_text(encoding="utf-8") == "MIT\n"


@requer_git
def test_restaura_item_e_manifesto_apagados_so_na_arvore(tmp_path: Path) -> None:
    raiz = _repo(tmp_path)
    (raiz / "LICENSE").unlink()
    (raiz / ".github" / "protected-files.txt").unlink()
    restaurador = _restaurador()

    assert restaurador.main([], raiz=raiz) == 1
    assert restaurador.main(["--aplicar"], raiz=raiz) == 0
    assert (raiz / "LICENSE").exists()
    assert (raiz / ".github" / "protected-files.txt").exists()


@requer_git
def test_tudo_no_lugar(tmp_path: Path) -> None:
    assert _restaurador().main([], raiz=_repo(tmp_path)) == 0
