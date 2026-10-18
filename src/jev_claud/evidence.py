"""Evidência local para o agente: leitura limitada, busca literal e sinais de falha.

Não infere causa raiz, não executa comandos e não usa modelos. As linhas são do
arquivo original; ANSI é removido somente para reconhecer e agrupar sinais.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_CHARS = 2_000_000
DEFAULT_CHARS = 400_000
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ERROR = re.compile(
    r"^\s*(?:FAILED\b|FAIL(?:\s|$)|ERROR(?:\s|:)|--- FAIL:|E\s{2,}\S|"
    r"error(?:\[|:|\s+[A-Z]+\d)|npm (?:ERR!|error\b))|"
    r"\berror(?:\[|:|\s+[A-Z]+\d)|\b\w*(?:Error|Exception):|\b(?:fatal error|panic:|Build FAILED)\b|"
    r"\b[1-9]\d* (?:failed|failures|errors)\b",
    re.IGNORECASE,
)
_WARNING = re.compile(r"\b(?:warning|warn)\b", re.IGNORECASE)
_SUMMARY = re.compile(
    r"\b\d+ (?:passed|failed|skipped)\b|Test Suites:|Tests:|test result:|BUILD (?:SUCCESS|FAILURE)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Source:
    path: str
    text: str
    size_bytes: int
    truncated: bool
    fingerprint: str

    def metadata(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "fingerprint": self.fingerprint,
            "fingerprint_kind": "file_identity_size_mtime",
            "size_bytes": self.size_bytes,
            "read_chars": len(self.text),
            "read_lines": len(self.text.splitlines()),
            "truncated": self.truncated,
            "coverage": "prefix" if self.truncated else "complete",
        }

    def excerpt(self, start: int, end: int, *, line_chars: int = 500) -> dict[str, Any]:
        lines = self.text.splitlines()
        end = min(end, len(lines))
        selected = lines[start - 1 : end]
        return {
            "start_line": start,
            "end_line": end,
            "lines": [
                {
                    "line": n,
                    "text": line[:line_chars],
                    "truncated": len(line) > line_chars
                    or (self.truncated and n == len(lines) and not self.text.endswith("\n")),
                }
                for n, line in enumerate(selected, start)
            ],
        }


def read_source(path: str, max_chars: int = DEFAULT_CHARS) -> Source:
    if not 1 <= max_chars <= MAX_CHARS:
        raise ValueError(f"max_chars deve estar entre 1 e {MAX_CHARS}.")
    file = Path(path).expanduser().resolve()
    if not file.is_file():
        raise ValueError(f"Arquivo não encontrado: {file}")
    with file.open(encoding="utf-8", errors="replace") as stream:
        before = os.fstat(stream.fileno())
        text = stream.read(max_chars + 1)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("O arquivo mudou durante a leitura; repita após a execução terminar.")
    # A identidade também detecta substituição do arquivo entre chamadas.
    identity = f"{file}:{after.st_dev}:{after.st_ino}:{after.st_size}:{after.st_mtime_ns}"
    return Source(
        str(file),
        text[:max_chars],
        after.st_size,
        len(text) > max_chars,
        hashlib.sha256(identity.encode()).hexdigest(),
    )


def read_evidence(
    path: str,
    *,
    start_line: int = 1,
    line_count: int = 40,
    fingerprint: str | None = None,
    max_chars: int = DEFAULT_CHARS,
) -> dict[str, Any]:
    if start_line < 1 or not 1 <= line_count <= 100:
        raise ValueError("start_line >= 1; line_count entre 1 e 100.")
    source = read_source(path, max_chars)
    if fingerprint is not None and fingerprint != source.fingerprint:
        raise ValueError("Evidência desatualizada: o arquivo mudou. Repita o diagnóstico ou a busca.")
    if start_line > len(source.text.splitlines()):
        raise ValueError("Linha fora da região lida; confira coverage e aumente max_chars se necessário.")
    excerpt = source.excerpt(start_line, start_line + line_count - 1, line_chars=2000)
    return {
        "source": source.metadata(),
        "evidence": excerpt,
        "next_line": excerpt["end_line"] + 1 if excerpt["end_line"] < len(source.text.splitlines()) else None,
    }


def search_file(
    path: str,
    query: str,
    *,
    max_results: int = 8,
    max_chars: int = DEFAULT_CHARS,
) -> dict[str, Any]:
    """Busca literal sem distinguir maiúsculas, com duas linhas de contexto."""
    if not query.strip() or not 1 <= max_results <= 20:
        raise ValueError("Informe query não vazia e max_results entre 1 e 20.")
    source = read_source(path, max_chars)
    matches = [n for n, line in enumerate(source.text.splitlines(), 1) if query.casefold() in line.casefold()]
    return {
        "source": source.metadata(),
        "method": "literal",
        "matches_in_read_region": len(matches),
        "omitted_matches": max(0, len(matches) - max_results),
        "evidence": [source.excerpt(max(1, n - 2), n + 2) for n in matches[:max_results]],
        "needs_agent_review": source.truncated or not matches,
    }


def diagnose_log(
    path: str,
    *,
    exit_code: int | None = None,
    max_groups: int = 12,
    max_chars: int = DEFAULT_CHARS,
) -> dict[str, Any]:
    """Agrupa mensagens idênticas; ocorrência é uma linha, não um teste distinto."""
    if not 1 <= max_groups <= 30:
        raise ValueError("max_groups deve estar entre 1 e 30.")
    source = read_source(path, max_chars)
    groups: dict[tuple[str, str], list[int]] = {}
    summaries: list[int] = []
    for n, line in enumerate(source.text.splitlines(), 1):
        clean = _ANSI.sub("", line).strip()
        if _SUMMARY.search(clean):
            summaries.append(n)
        # Zero failures/errors é relatório, não falha. Ainda reconhece outros sinais na linha.
        signal = re.sub(r"\b0 (?:failed|failures|errors)\b", "", clean, flags=re.IGNORECASE)
        kind = "error" if _ERROR.search(signal) else "warning" if _WARNING.search(signal) else None
        if kind:
            groups.setdefault((kind, clean), []).append(n)
    ordered = sorted(groups.items(), key=lambda item: (item[0][0] != "error", item[1][0]))
    reasons = []
    if exit_code is None:
        reasons.append("exit_code_unknown")
    elif exit_code != 0:
        reasons.append("command_failed")
    if any(kind == "error" for kind, _ in groups):
        reasons.append("error_signals")
    if source.truncated:
        reasons.append("incomplete_source")
    if len(groups) > max_groups:
        reasons.append("omitted_groups")
    if not source.text.strip():
        reasons.append("empty_log")
    return {
        "status": "needs_agent_review" if reasons else "no_failure_observed",
        "needs_agent_review": bool(reasons),
        "reasons": reasons,
        "source": source.metadata(),
        "exit_code": exit_code,
        "method": "deterministic_signals",
        "root_cause": None,
        "groups": [
            {
                "kind": kind,
                "message": message[:500],
                "message_truncated": len(message) > 500,
                "occurrences": len(numbers),
                "evidence": source.excerpt(max(1, numbers[0] - 2), numbers[0] + 2),
            }
            for (kind, message), numbers in ordered[:max_groups]
        ],
        "groups_in_read_region": len(groups),
        "omitted_groups": max(0, len(groups) - max_groups),
        "summaries": [source.excerpt(n, n) for n in summaries[-5:]],
        "next_action": "O agente confere as evidências, lê o código relevante, corrige e executa os testes."
        if reasons
        else "Nenhuma falha observada no log e código de saída informado; valide o escopo dos testes.",
    }
