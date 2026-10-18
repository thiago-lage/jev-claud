"""Casos observáveis: log -> evidência -> leitura -> nova execução, sem modelos."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from jev_claud import JevClaud, choice, noul
from jev_claud.cli import main
from jev_claud.compact import save_log, write_compacted
from jev_claud.evidence import diagnose_log, read_evidence, read_source, search_file
from jev_claud.mcp import MAX_FILE_CHARS, Server
from test_mcp import Recorder, call, payload


def test_log_diagnosis_preserves_exact_evidence_and_groups_duplicates(tmp_path: Path) -> None:
    log = tmp_path / "test.log"
    lines = [
        "coletando",
        "tests/test_api.py:42",
        "E       AssertionError: esperado 200, veio 500",
        "E       AssertionError: esperado 200, veio 500",
        "===== 1 failed, 3 passed =====",
    ]
    log.write_text("\n".join(lines), encoding="utf-8")
    report = diagnose_log(str(log), exit_code=1)
    assert report["needs_agent_review"] and report["root_cause"] is None
    group = next(g for g in report["groups"] if "AssertionError" in g["message"])
    assert group["occurrences"] == 2
    for line in group["evidence"]["lines"]:
        assert line["text"] == lines[line["line"] - 1]
    evidence = read_evidence(
        str(log), start_line=2, line_count=2, fingerprint=report["source"]["fingerprint"]
    )
    assert evidence["evidence"]["lines"][1]["text"] == lines[2]
    assert evidence["next_line"] == 4


@pytest.mark.parametrize(
    ("content", "exit_code", "review"),
    [
        ("4 passed, 0 failed, 0 errors", 0, False),
        ("4 passed", None, True),
        ("mensagem desconhecida", 2, True),
        ("", 0, True),
        ("AssertionError: falhou", 0, True),
        ("warning: deprecated\n4 passed", 0, False),
    ],
)
def test_no_errors_is_not_automatically_success(
    tmp_path: Path,
    content: str,
    exit_code: int | None,
    review: bool,
) -> None:
    log = tmp_path / "test.log"
    log.write_text(content, encoding="utf-8")
    result = diagnose_log(str(log), exit_code=exit_code)
    assert result["needs_agent_review"] is review


@pytest.mark.parametrize(
    "message",
    [
        "FAILED tests/test_api.py::test_login - AssertionError",
        "FAIL src/login.test.ts",
        "--- FAIL: TestLogin (0.00s)",
        "src/app.ts(2,3): error TS2322: Type mismatch",
        "error[E0308]: mismatched types",
        "npm ERR! missing script: test",
        "Build FAILED.",
        "\x1b[31mTypeError: bad value\x1b[0m",
    ],
)
def test_recognizes_common_failure_formats(tmp_path: Path, message: str) -> None:
    log = tmp_path / "test.log"
    log.write_text(message, encoding="utf-8")
    report = diagnose_log(str(log), exit_code=1)
    assert report["groups"][0]["kind"] == "error"
    assert report["groups"][0]["evidence"]["lines"][0]["text"] == message


def test_truncation_and_omissions_are_explicit(tmp_path: Path) -> None:
    log = tmp_path / "test.log"
    log.write_text("TypeError: a\nTypeError: b\n" + "x" * 900, encoding="utf-8")
    report = diagnose_log(str(log), max_chars=60, max_groups=1, exit_code=0)
    assert report["source"]["truncated"] and report["source"]["read_chars"] == 60
    assert report["source"]["coverage"] == "prefix" and report["omitted_groups"] == 1
    assert "incomplete_source" in report["reasons"]
    assert "omitted_groups" in report["reasons"]


def test_search_and_read_detect_changed_source(tmp_path: Path) -> None:
    file = tmp_path / "source.txt"
    file.write_text("antes\nErro de conexão\ndepois\nErro repetido", encoding="utf-8")
    found = search_file(str(file), "ERRO", max_results=1)
    assert found["matches_in_read_region"] == 2 and found["omitted_matches"] == 1
    assert found["evidence"][0]["lines"][1]["text"] == "Erro de conexão"
    file.write_text("alterado", encoding="utf-8")
    with pytest.raises(ValueError, match="desatualizada"):
        read_evidence(str(file), fingerprint=found["source"]["fingerprint"])


def test_long_lines_and_invalid_limits(tmp_path: Path) -> None:
    file = tmp_path / "log"
    file.write_text("TypeError: " + "x" * 10000, encoding="utf-8")
    report = diagnose_log(str(file), exit_code=1)
    assert report["groups"][0]["message_truncated"]
    assert report["groups"][0]["evidence"]["lines"][0]["truncated"]
    for limit in (0, -1, 2_000_001):
        with pytest.raises(ValueError):
            read_source(str(file), limit)
    with pytest.raises(ValueError):
        read_evidence(str(file), start_line=0)


def test_compaction_fails_open_when_full_log_cannot_be_saved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("jev_claud.compact.save_log", lambda *a, **kw: None)
    original = "\n".join(f"line {i}" for i in range(300))
    output = io.StringIO()
    write_compacted(original, output, log_dir=tmp_path)
    assert output.getvalue() == original


def test_logs_do_not_overwrite_another_run_in_same_second(tmp_path: Path) -> None:
    first = save_log("first", tmp_path)
    second = save_log("second", tmp_path)
    assert first is not None and second is not None and first != second
    assert first.read_text() == "first"


def test_mcp_end_to_end_diagnose_read_and_validate_new_log(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text("FAILED test_api\nAssertionError: esperado 200", encoding="utf-8")
    server = Server(lambda: JevClaud(jev=None, writer=None, store=":memory:"))
    try:
        report = payload(call(server, "diagnose_log", {"path": str(log), "exit_code": 1}))
        assert report["status"] == "needs_agent_review"
        expanded = payload(
            call(
                server,
                "read_evidence",
                {
                    "path": str(log),
                    "fingerprint": report["source"]["fingerprint"],
                },
            )
        )
        assert "AssertionError" in expanded["evidence"]["lines"][1]["text"]
        log.write_text("1 passed", encoding="utf-8")
        assert call(
            server,
            "read_evidence",
            {
                "path": str(log),
                "fingerprint": report["source"]["fingerprint"],
            },
        )["isError"]
        passed = payload(call(server, "diagnose_log", {"path": str(log), "exit_code": 0}))
        assert passed["status"] == "no_failure_observed"
        status = payload(call(server, "status", {}))
        assert [event["status"] for event in status["recent_activity"]] == ["ok", "ok", "error", "ok"]
    finally:
        server.close()


def test_cli_diagnosis_is_available_without_ollama(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "build.log"
    log.write_text("error: missing dependency", encoding="utf-8")
    assert main(["diagnose", str(log), "--exit-code", "1", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["needs_agent_review"]
    assert main(["diagnose", str(log)]) == 0
    assert "missing dependency" in capsys.readouterr().out


@pytest.mark.parametrize(
    "text",
    [
        "Não é cobrança, nem fatura, nem pagamento, nem reembolso. É um bug.",
        "It is not billing or payment; it is a bug.",
        "Não quero reembolso",
        "I don't want a refund",
    ],
)
def test_negation_cannot_be_accepted_as_confident_semantic_decision(text: str) -> None:
    with JevClaud(jev=None, writer=None, store=":memory:") as app:
        decisions = app.decide(
            text,
            {
                "department": choice(
                    "Setor?",
                    {
                        "financeiro": "cobrança fatura pagamento reembolso billing payment",
                        "suporte": "bug erro",
                    },
                ),
                "refund": noul("Quer reembolso ou refund?"),
            },
        )
        assert all(d.uncertain for d in decisions.values())


def test_mcp_abstains_instead_of_counting_unsupported_labels() -> None:
    server = Server(lambda: JevClaud(jev=None, writer=None, store=":memory:"))
    try:
        result = payload(
            call(
                server,
                "decide",
                {
                    "state": "Não quero reembolso",
                    "questions": {
                        "q": {"type": "noul", "instructions": "Quer reembolso?"},
                    },
                },
            )
        )
        assert result["decisions"]["q"]["value"] is None
        assert result["decisions"]["q"]["abstained"] and result["needs_agent_review"]
        confident = payload(
            call(
                server,
                "decide",
                {
                    "state": "cobrança fatura pagamento",
                    "questions": {
                        "q": {
                            "type": "choice",
                            "instructions": "Setor?",
                            "options": {"financeiro": "cobrança fatura pagamento", "suporte": "bug"},
                        },
                    },
                },
            )
        )
        assert confident["decisions"]["q"]["band"] == "high"
        assert confident["needs_agent_review"]  # alta coincidência lexical não verifica semântica
        classified = payload(
            call(
                server,
                "classify",
                {
                    "items": ["bom dia", "não é cobrança"],
                    "instructions": "Setor?",
                    "options": {"financeiro": "cobrança", "suporte": "bug"},
                },
            )
        )
        assert classified["counts"] == {} and classified["uncertain_total"] == 2
    finally:
        server.close()


def test_mcp_rejects_silent_cutting_of_decision_input(tmp_path: Path) -> None:
    file = tmp_path / "big.txt"
    file.write_text("x" * (MAX_FILE_CHARS + 1), encoding="utf-8")
    server = Server()
    result = call(
        server,
        "decide",
        {
            "file": str(file),
            "questions": {
                "q": {"type": "noul", "instructions": "Algo?"},
            },
        },
    )
    assert result["isError"] and "Nenhum item" in result["content"][0]["text"]


def test_summary_cache_and_coverage(tmp_path: Path) -> None:
    from jev_claud import CallableWriter

    file = tmp_path / "report.txt"
    file.write_text("Primeiro fato.\nOutro fato.", encoding="utf-8")
    llm = Recorder()
    server = Server(lambda: JevClaud(jev=None, writer=CallableWriter(llm), store=":memory:"))
    try:
        args = {"path": str(file), "max_chars": 14}
        first = payload(call(server, "summarize_file", args))
        assert first["source"]["truncated"] and first["verification"] == "unverified"
        second = payload(call(server, "summarize_file", args))
        assert second["cached"] and len(llm.calls) == 1
        payload(call(server, "summarize_file", {**args, "instructions": "Outro foco"}))
        assert len(llm.calls) == 2
        file.write_text("Novo fato.\nMais evidências.", encoding="utf-8")
        third = payload(call(server, "summarize_file", args))
        assert not third["cached"] and len(llm.calls) == 3
        refreshed = payload(call(server, "summarize_file", {**args, "cache": False}))
        assert not refreshed["cached"] and len(llm.calls) == 4
    finally:
        server.close()
