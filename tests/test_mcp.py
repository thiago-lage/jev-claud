"""Servidor MCP: protocolo JSON-RPC, ferramentas e loop stdio, sem rede."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from jev_claude import CallableWriter, JevClaude
from jev_claude.mcp import CHUNK_CHARS, MAX_ITEMS, TOOLS, Server, serve


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, prompt: str, system: str | None) -> str:
        self.calls.append((prompt, system))
        return f"resposta {len(self.calls)}"


@pytest.fixture
def llm() -> Recorder:
    return Recorder()


@pytest.fixture
def server(llm: Recorder) -> Server:
    srv = Server(lambda: JevClaude(store=":memory:", writer=CallableWriter(llm)))
    yield srv  # type: ignore[misc]
    srv.close()


def call(server: Server, name: str, arguments: dict[str, Any], msg_id: int = 1) -> dict[str, Any]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    assert response is not None and "result" in response, response
    return response["result"]


def payload(result: dict[str, Any]) -> Any:
    assert result["isError"] is False, result
    text = result["content"][0]["text"]
    try:
        return json.loads(text)
    except ValueError:
        return text


# -- protocolo ---------------------------------------------------------------------------


def test_initialize_echoes_protocol_and_explains_token_savings(server: Server) -> None:
    response = server.handle(
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}
    )
    assert response is not None
    result = response["result"]
    assert result["protocolVersion"] == "2025-11-25"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "jev-claude"
    assert "economizar tokens" in result["instructions"]


def test_tools_list_has_schemas(server: Server) -> None:
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response is not None
    names = [tool["name"] for tool in response["result"]["tools"]]
    assert names == ["decide", "classify", "rank", "summarize_file", "write", "status"]
    assert all(tool["inputSchema"]["type"] == "object" and tool["description"] for tool in TOOLS)


def test_notifications_get_no_response_and_unknown_methods_error(server: Server) -> None:
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}}) is None
    unknown = server.handle({"jsonrpc": "2.0", "id": 7, "method": "resources/list"})
    assert unknown is not None and unknown["error"]["code"] == -32601
    assert server.handle({"jsonrpc": "2.0", "id": 8, "method": "ping"}) == {
        "jsonrpc": "2.0",
        "id": 8,
        "result": {},
    }
    invalid = server.handle({"id": 9, "method": "ping"})
    assert invalid is not None and invalid["error"]["code"] == -32600


def test_tool_errors_are_reported_to_the_model(server: Server) -> None:
    assert call(server, "nao_existe", {})["isError"] is True
    result = call(
        server, "decide", {"state": "x", "questions": {"q": {"type": "talvez", "instructions": "?"}}}
    )
    assert result["isError"] is True and "choice, noul, score ou box" in result["content"][0]["text"]
    assert call(server, "classify", {"file": "nao/existe.txt", "instructions": "?", "options": {"a": None}})[
        "isError"
    ]


# -- ferramentas ---------------------------------------------------------------------------


def test_decide_returns_compact_typed_answers(server: Server) -> None:
    result = payload(
        call(
            server,
            "decide",
            {
                "state": "Fui cobrado duas vezes na fatura, é urgente!",
                "questions": {
                    "setor": {
                        "type": "choice",
                        "instructions": "Qual setor?",
                        "options": {"financeiro": "cobrança, fatura", "suporte": "erro, bug"},
                    },
                    "urgente": {
                        "type": "noul",
                        "instructions": "É urgente?",
                        "yes": "urgente",
                        "no": "sem pressa",
                    },
                    "raiva": {"type": "score", "instructions": "Raiva?", "levels": ["calmo", "furioso"]},
                },
            },
        )
    )
    assert result["source"] == "offline" and result["cost_usd"] == 0
    assert result["decisions"]["setor"]["value"] == "financeiro"
    assert result["decisions"]["urgente"]["value"] is True and "p_yes" in result["decisions"]["urgente"]
    assert "level" in result["decisions"]["raiva"]


def test_decide_reads_state_from_file(server: Server, tmp_path: Path) -> None:
    file = tmp_path / "ticket.txt"
    file.write_text("O aplicativo dá erro e trava com um bug.", encoding="utf-8")
    q = {
        "type": "choice",
        "instructions": "Setor?",
        "options": {"financeiro": "fatura", "suporte": "erro, bug"},
    }
    result = payload(call(server, "decide", {"file": str(file), "questions": {"setor": q}}))
    assert result["decisions"]["setor"]["value"] == "suporte"


def test_classify_file_returns_counts_not_data(server: Server, tmp_path: Path) -> None:
    lines = ["Fui cobrado duas vezes na fatura"] * 30 + ["O app dá erro, bug na tela"] * 25 + ["Bom dia"] * 5
    file = tmp_path / "tickets.txt"
    file.write_text("\n".join(lines) + "\n\n", encoding="utf-8")
    result = payload(
        call(
            server,
            "classify",
            {
                "file": str(file),
                "instructions": "Qual setor deve atender?",
                "options": {"financeiro": "cobrança, fatura", "suporte": "erro, bug, falha"},
            },
        )
    )
    assert result["total"] == 60
    # "Bom dia" não tem evidência: empate uniforme, cai no primeiro rótulo com confiança 0.
    assert result["counts"] == {"financeiro": 35, "suporte": 25}
    assert result["uncertain_total"] == 5 and result["uncertain"][0]["text"] == "Bom dia"
    assert result["items"] is None  # mais de 50 itens: só o resumo volta para o contexto


def test_classify_items_and_limits(server: Server) -> None:
    result = payload(
        call(
            server,
            "classify",
            {"items": ["fatura", "bug"], "instructions": "?", "options": {"fatura": None, "bug": None}},
        )
    )
    assert [item["label"] for item in result["items"]] == ["fatura", "bug"]
    too_many = call(
        server, "classify", {"items": ["x"] * (MAX_ITEMS + 1), "instructions": "?", "options": {"a": None}}
    )
    assert too_many["isError"] is True


def test_rank_returns_only_top_k(server: Server, tmp_path: Path) -> None:
    file = tmp_path / "docs.txt"
    file.write_text(
        "receita de bolo\ncomo cancelar a assinatura\nplanos e preços\nhorário de atendimento\n", "utf-8"
    )
    result = payload(call(server, "rank", {"file": str(file), "query": "cancelar assinatura", "top_k": 2}))
    assert result["total"] == 4 and len(result["top"]) == 2
    assert result["top"][0]["text"] == "como cancelar a assinatura"


def test_write_uses_local_llm(server: Server, llm: Recorder) -> None:
    text = payload(
        call(server, "write", {"prompt": "Diga olá", "context": {"cliente": "Ana"}, "system": "Breve."})
    )
    assert text == "resposta 1"
    prompt, system = llm.calls[0]
    assert prompt.startswith("Diga olá") and '"cliente": "Ana"' in prompt and system == "Breve."
    assert call(server, "write", {"prompt": "  "})["isError"] is True


def test_summarize_small_file_in_one_call(server: Server, llm: Recorder, tmp_path: Path) -> None:
    file = tmp_path / "nota.txt"
    file.write_text("Reunião: decidimos lançar na sexta.", encoding="utf-8")
    assert payload(call(server, "summarize_file", {"path": str(file)})) == "resposta 1"
    assert len(llm.calls) == 1 and "decidimos lançar" in llm.calls[0][0]


def test_summarize_large_file_map_reduce(server: Server, llm: Recorder, tmp_path: Path) -> None:
    file = tmp_path / "grande.txt"
    file.write_text("palavra " * (CHUNK_CHARS // 8 * 3), encoding="utf-8")  # ~3 partes
    summary = payload(call(server, "summarize_file", {"path": str(file), "instructions": "Só decisões."}))
    parts = len(llm.calls) - 1
    assert parts >= 3
    assert summary == f"resposta {parts + 1}"
    assert "Junte estes resumos parciais" in llm.calls[-1][0] and "Só decisões." in llm.calls[-1][0]


def test_status_reports_backends(server: Server) -> None:
    result = payload(call(server, "status", {}))
    assert result["jev"]["enabled"] is False and result["writer"] == "callable"


# -- loop stdio ------------------------------------------------------------------------------


def test_serve_loop_over_bytes(llm: Recorder) -> None:
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "write", "arguments": {"prompt": "olá ç"}},
        },
    ]
    raw = b"\n".join(json.dumps(m, ensure_ascii=False).encode("utf-8") for m in messages) + b"\n\n{quebrado\n"
    out = io.BytesIO()
    server = Server(lambda: JevClaude(store=":memory:", writer=CallableWriter(llm)))
    assert serve(io.BytesIO(raw), out, server) == 0
    responses = [json.loads(line) for line in out.getvalue().decode("utf-8").splitlines()]
    assert [r.get("id") for r in responses] == [1, 2, None]
    assert responses[1]["result"]["content"][0]["text"] == "resposta 1"
    assert responses[2]["error"]["code"] == -32700
    assert llm.calls[0][0] == "olá ç"
