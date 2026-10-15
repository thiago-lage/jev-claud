"""Mascote, banner e economia de tokens mostrada no terminal."""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from jev_claude import CallableWriter, JevClaude
from jev_claude.cli import main
from jev_claude.compact import run_filter
from jev_claude.mascot import ASCII, render
from jev_claude.mcp import Server
from jev_claude.savings import COMPACT, TOOLS, human_tokens, record, summary, tokens_from_chars
from jev_claude.store import Store
from jev_claude.wizard import UI

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def test_mascot_renders_as_blocks_or_ascii() -> None:
    blocks = render()
    assert len(blocks) == 4 and len({len(line) for line in blocks}) == 1
    assert set("".join(blocks)) <= set(" █▀▄")
    assert render(fancy=False) == list(ASCII)


@pytest.mark.parametrize(
    ("tokens", "text"),
    [
        (0, "0"),
        (999, "999"),
        (1000, "1 mil"),
        (12_400, "12,4 mil"),
        (123_456, "123 mil"),
        (1_250_000, "1,2 mi"),
    ],
)
def test_human_tokens(tokens: int, text: str) -> None:
    assert human_tokens(tokens) == text


def test_summary_separates_claude_and_jev() -> None:
    store = Store(":memory:")
    assert summary(store) == "nenhuma economia registrada ainda este mês"
    record(COMPACT, 40_000, store)  # 40 mil caracteres ~ 10 mil tokens
    assert summary(store) == "economizou este mês: ~10 mil tokens do Claude"
    store.record_routing("offline", 3, 2_500)
    assert summary(store) == "economizou este mês: ~10 mil tokens do Claude · ~2,5 mil do Jev"
    record(TOOLS, -50, store)  # resposta maior que o arquivo: nada a registrar
    assert store.savings()[COMPACT] == (1, 10_000) and TOOLS not in store.savings()
    assert tokens_from_chars(-10) == 0
    store.close()


def test_record_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = tmp_path / "arquivo"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("JEV_CLAUDE_HOME", str(blocker / "sub"))  # não dá para criar a pasta
    record(COMPACT, 10_000)


def test_compaction_records_claude_savings(tmp_path: Path) -> None:
    text = "\n".join(f"tests/test_x.py::test_{i} PASSED" for i in range(400))
    run_filter(io.StringIO(text), io.StringIO(), log_dir=tmp_path)
    store = Store()
    events, tokens = store.savings()[COMPACT]
    store.close()
    assert events == 1 and tokens > 2_000


def test_mcp_file_tools_record_claude_savings(tmp_path: Path) -> None:
    data = tmp_path / "tickets.txt"
    data.write_text("\n".join(f"Fui cobrado duas vezes na fatura {i}" for i in range(300)), encoding="utf-8")
    server = Server(lambda: JevClaude(store=":memory:", writer=CallableWriter(lambda p, s: "ok")))
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "classify",
                "arguments": {
                    "file": str(data),
                    "instructions": "Setor?",
                    "options": {"financeiro": "fatura, cobrança", "suporte": "erro"},
                    "return_items": False,
                },
            },
        }
    )
    assert response is not None and response["result"]["isError"] is False
    events, tokens = server.app.store.savings()[TOOLS]
    assert events == 1 and tokens > 1_000
    server.close()


def test_banner_box_is_aligned_with_mascot_and_footer() -> None:
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")  # terminal UTF-8: mascote em blocos
    UI(stream, interactive=False, color=True).banner(
        "9.9.9", footer="economizou este mês: ~1 mil tokens do Claude"
    )
    stream.flush()
    text = stream.buffer.getvalue().decode("utf-8")  # type: ignore[attr-defined]
    lines = [ANSI.sub("", line) for line in text.splitlines()]
    box = [line for line in lines if line and line[0] in "╭│╰"]
    assert len({len(line) for line in box}) == 1  # todas as linhas da caixa com a mesma largura
    assert any("█" in line and "Bem-vindo" not in line for line in box)
    assert "v9.9.9" in text
    assert "\x1b[2m  economizou este mês: ~1 mil tokens do Claude\x1b[0m" in text  # cinza


def test_banner_falls_back_to_ascii() -> None:
    class Ascii(io.StringIO):
        encoding = "ascii"

    out = Ascii()
    UI(out, interactive=False, color=False).banner("1.0.0")
    assert "[o_o]" in out.getvalue() and "█" not in out.getvalue()


def test_doctor_and_spend_show_savings(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    run_filter(io.StringIO("\n".join(f"linha {i}" for i in range(500))), io.StringIO(), log_dir=tmp_path)
    capsys.readouterr()
    assert main(["spend"]) == 0
    assert "economizou este mês: ~" in capsys.readouterr().out
    assert main(["doctor"]) == 0
    assert "tokens do Claude" in capsys.readouterr().out
