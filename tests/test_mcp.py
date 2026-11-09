"""Servidor MCP: protocolo JSON-RPC, ferramentas e loop stdio, sem rede."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from jev_claud import CallableWriter, JevClaud
from jev_claud.effort import EFFORTS, PLANS
from jev_claud.mcp import MAX_ITEMS, TOOLS, Server, serve

CHUNK_CHARS = PLANS["medio"].chunk_chars


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
    srv = Server(lambda: JevClaud(store=":memory:", writer=CallableWriter(llm)))
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
    assert result["serverInfo"]["name"] == "jev-claud"
    assert "economizar tokens" in result["instructions"]


def test_tools_list_has_schemas(server: Server) -> None:
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response is not None
    names = [tool["name"] for tool in response["result"]["tools"]]
    assert names == [
        "decide",
        "classify",
        "rank",
        "summarize_file",
        "write",
        "status",
        "diagnose_log",
        "search_file",
        "read_evidence",
    ]
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
    # Sem evidência, o palpite não entra nas contagens como uma classificação aceita.
    assert result["counts"] == {"financeiro": 30, "suporte": 25}
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


def test_write_uses_the_session_llm(server: Server, llm: Recorder) -> None:
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
    assert payload(call(server, "summarize_file", {"path": str(file)}))["summary"] == "resposta 1"
    assert len(llm.calls) == 1 and "decidimos lançar" in llm.calls[0][0]


def test_summarize_large_file_map_reduce(server: Server, llm: Recorder, tmp_path: Path) -> None:
    file = tmp_path / "grande.txt"
    file.write_text("palavra " * (CHUNK_CHARS // 8 * 3), encoding="utf-8")  # ~3 partes
    summary = payload(call(server, "summarize_file", {"path": str(file), "instructions": "Só decisões."}))
    parts = len(llm.calls) - 1
    assert parts >= 3
    assert summary["summary"] == f"resposta {parts + 1}"
    assert "Junte estes resumos parciais" in llm.calls[-1][0] and "Só decisões." in llm.calls[-1][0]


def test_summarize_effort_changes_how_much_work_is_done(llm: Recorder, tmp_path: Path) -> None:
    file = tmp_path / "grande.txt"
    file.write_text("Uma frase com fatos. " * 1500, encoding="utf-8")  # ~31 mil caracteres
    calls = {}
    for effort in EFFORTS:
        recorder = Recorder()
        srv = Server(lambda r=recorder: JevClaud(store=":memory:", writer=CallableWriter(r)))
        payload(call(srv, "summarize_file", {"path": str(file), "effort": effort}))
        calls[effort] = len(recorder.calls)
        srv.close()
    assert calls["baixo"] < calls["medio"] < calls["alto"] < calls["extra"] < calls["max"]
    assert calls["ultracode"] == calls["max"] + 1  # a passada de revisão


def test_summarize_rejects_bad_effort_and_empty_file(server: Server, tmp_path: Path) -> None:
    file = tmp_path / "vazio.txt"
    file.write_text("  \n", encoding="utf-8")
    assert call(server, "summarize_file", {"path": str(file)})["isError"] is True
    file.write_text("texto", encoding="utf-8")
    result = call(server, "summarize_file", {"path": str(file), "effort": "turbo"})
    assert result["isError"] is True and "ultracode" in result["content"][0]["text"]


def test_effort_is_in_tool_schemas() -> None:
    schemas = {tool["name"]: tool["inputSchema"]["properties"] for tool in TOOLS}
    assert schemas["summarize_file"]["effort"]["enum"] == [
        "baixo",
        "medio",
        "alto",
        "extra",
        "max",
        "ultracode",
    ]
    assert "effort" in schemas["write"]


def test_status_reports_llm_cache_and_activity(server: Server, tmp_path: Path) -> None:
    file = tmp_path / "nota.txt"
    file.write_text("Reunião: decidimos lançar na sexta.", encoding="utf-8")
    payload(call(server, "summarize_file", {"path": str(file)}))
    result = payload(call(server, "status", {}))
    assert result["jev"]["enabled"] is False and result["offline"] is False
    assert result["llm"]["mode"] == "writer" and result["llm"]["writer"] == "callable"
    assert result["llm"]["sampling"] is False
    assert result["summary_cache"] == {"entries": 1, "scope": "server_session", "limit": 16}
    assert result["recent_activity"][-1]["tool"] == "summarize_file"
    assert "writer" not in result and "effort_models" not in result


# -- modo agente: host sem MCP sampling --------------------------------------------------------


@pytest.fixture
def agent() -> Server:
    srv = Server(lambda: JevClaud(store=":memory:"))
    initialize(srv, {"roots": {"listChanged": True}, "elicitation": {}})  # como o Claude Code
    yield srv  # type: ignore[misc]
    srv.close()


def initialize(server: Server, capabilities: dict[str, Any]) -> dict[str, Any]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": capabilities,
                "clientInfo": {"name": "host-teste", "version": "1.0"},
            },
        }
    )
    assert response is not None
    return response["result"]


def test_agent_mode_status_names_the_host(agent: Server) -> None:
    llm = payload(call(agent, "status", {}))["llm"]
    assert llm["mode"] == "agent" and llm["sampling"] is False and llm["writer"] is None
    assert llm["host"] == {"name": "host-teste", "version": "1.0"}
    assert "mode=agent" in llm["note"]


def test_agent_mode_write_returns_the_task(agent: Server) -> None:
    result = payload(call(agent, "write", {"prompt": "Diga olá", "system": "Breve.", "effort": "baixo"}))
    assert result["mode"] == "agent" and result["task"] == {
        "prompt": "Diga olá",
        "max_tokens": 400,
        "system": "Breve.",
    }
    assert "Nenhum outro modelo" in result["next_action"]


def test_agent_mode_summary_pages_blocks_and_caches_what_the_agent_wrote(
    agent: Server, tmp_path: Path
) -> None:
    file = tmp_path / "grande.txt"
    file.write_text("Uma frase com fatos. " * 3_000, encoding="utf-8")  # ~63 mil caracteres
    args = {"path": str(file), "max_chars": 100_000, "effort": "alto"}
    first = payload(call(agent, "summarize_file", args))
    assert first["mode"] == "agent" and first["summary"] is None
    assert first["plan"] == {"chunk_chars": 4_000, "overlap_chars": 500, "verify": False}
    assert first["blocks"][0]["n"] == 1 and first["next_block"] == len(first["blocks"]) + 1
    assert sum(len(b["text"]) for b in first["blocks"]) <= 24_000
    pages = [first]
    while pages[-1]["next_block"]:
        pages.append(payload(call(agent, "summarize_file", {**args, "block": pages[-1]["next_block"]})))
    numbers = [b["n"] for page in pages for b in page["blocks"]]
    assert numbers == list(range(1, first["blocks_total"] + 1))
    assert call(agent, "summarize_file", {**args, "block": 999})["isError"] is True

    saved = payload(call(agent, "summarize_file", {**args, "save_summary": "  Resumo do agente.  "}))
    assert saved["saved"] is True and saved["summary_cache"]["entries"] == 1
    again = payload(call(agent, "summarize_file", args))
    assert again["summary"] == "Resumo do agente." and again["cached"] is True
    other_effort = payload(call(agent, "summarize_file", {**args, "effort": "baixo"}))
    assert other_effort["mode"] == "agent"  # outro esforço, outro resumo
    assert call(agent, "summarize_file", {**args, "save_summary": " "})["isError"] is True


def test_agent_mode_ultracode_asks_for_a_review(agent: Server, tmp_path: Path) -> None:
    file = tmp_path / "nota.txt"
    file.write_text("Reunião: decidimos lançar na sexta.", encoding="utf-8")
    result = payload(call(agent, "summarize_file", {"path": str(file), "effort": "ultracode"}))
    assert result["blocks_total"] == 1 and result["next_block"] is None
    assert any("Revise o resumo final" in step for step in result["instructions"])


def test_agent_mode_returns_uncertain_items_to_the_agent(agent: Server) -> None:
    result = payload(
        call(
            agent,
            "classify",
            {
                "items": ["fui cobrado duas vezes", "bom dia"],
                "instructions": "Setor?",
                "options": {"financeiro": "cobrança, fatura", "suporte": "erro, bug"},
            },
        )
    )
    assert result["llm"] == "agent" and result["uncertain_total"] >= 1
    assert "classifique você" in result["next_action"]
    decided = payload(
        call(
            agent,
            "decide",
            {"state": "bom dia", "questions": {"q": {"type": "noul", "instructions": "Urgente?"}}},
        )
    )
    assert decided["needs_agent_review"] is True and "decida você" in decided["next_action"]


# -- LLM da sessão ---------------------------------------------------------------------------


class JsonLLM:
    """LLM da sessão que responde JSON, como o host responderia a um sampling."""

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def __call__(self, prompt: str, system: str | None) -> str:
        self.prompts.append(prompt)
        answer = self.answer(prompt) if callable(self.answer) else self.answer
        return "Claro! " + json.dumps(answer, ensure_ascii=False)


def _session(answer: Any) -> tuple[Server, JsonLLM]:
    llm = JsonLLM(answer)
    return Server(lambda: JevClaud(store=":memory:", writer=CallableWriter(llm))), llm


def test_session_llm_resolves_uncertain_classifications() -> None:
    server, llm = _session({"1": "suporte", "2": "inexistente"})
    result = payload(
        call(
            server,
            "classify",
            {
                "items": ["fui cobrado duas vezes na fatura da cobrança", "bom dia", "olá"],
                "instructions": "Setor?",
                "options": {"financeiro": "cobrança, fatura", "suporte": "erro, bug"},
            },
        )
    )
    items = {item["i"]: item for item in result["items"]}
    assert items[1]["label"] == "suporte" and items[1]["confidence"] is None
    assert items[2]["label"] is None  # rótulo fora da lista: continua incerto
    assert result["sources"]["session"] == 1 and result["llm"] == "writer"
    assert "1: bom dia" in llm.prompts[0] and "- financeiro: cobrança, fatura" in llm.prompts[0]
    server.close()


def test_session_llm_answers_uncertain_decisions() -> None:
    server, _ = _session({"urgente": "sim", "setor": "suporte", "nivel": "alto", "extra": "talvez"})
    result = payload(
        call(
            server,
            "decide",
            {
                "state": "bom dia",
                "questions": {
                    "urgente": {"type": "noul", "instructions": "Pede ação imediata?"},
                    "setor": {
                        "type": "choice",
                        "instructions": "Setor?",
                        "options": {"financeiro": None, "suporte": None},
                    },
                    "nivel": {
                        "type": "score",
                        "instructions": "Gravidade?",
                        "levels": ["baixo", "médio", "alto"],
                    },
                    "extra": {"type": "noul", "instructions": "Elogio?"},
                },
            },
        )
    )
    decisions = result["decisions"]
    assert decisions["urgente"]["value"] is True and decisions["urgente"]["source"] == "session"
    assert decisions["setor"]["value"] == "suporte" and "candidate" not in decisions["setor"]
    assert decisions["nivel"]["value"] == 2 and decisions["nivel"]["level"] == 2
    assert sorted(result["session_resolved"]) == ["nivel", "setor", "urgente"]
    assert result["review_questions"] == ["extra"] and result["needs_agent_review"] is True
    server.close()


def test_session_llm_reorders_rank_candidates() -> None:
    server, llm = _session({"top": [3, 3, 99, 0]})
    items = ["receita de bolo", "como cancelar a assinatura", "planos e preços", "cancelamento e reembolso"]
    result = payload(call(server, "rank", {"items": items, "query": "cancelar assinatura", "top_k": 2}))
    assert [hit["i"] for hit in result["top"]] == [3, 0]
    assert result["source"] == "session" and result["needs_agent_review"] is False
    assert "Consulta: cancelar assinatura" in llm.prompts[0]
    server.close()


def test_session_llm_without_json_keeps_the_offline_answer(server: Server) -> None:
    result = payload(call(server, "rank", {"items": ["a b", "c d", "a"], "query": "a", "top_k": 1}))
    assert result["source"] != "session" and result["needs_agent_review"] is True


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
    server = Server(lambda: JevClaud(store=":memory:", writer=CallableWriter(llm)))
    assert serve(io.BytesIO(raw), out, server) == 0
    responses = [json.loads(line) for line in out.getvalue().decode("utf-8").splitlines()]
    assert [r.get("id") for r in responses] == [1, 2, None]
    assert responses[1]["result"]["content"][0]["text"] == "resposta 1"
    assert responses[2]["error"]["code"] == -32700
    assert llm.calls[0][0] == "olá ç"


def _frames(*messages: dict[str, Any]) -> bytes:
    return b"".join(json.dumps(m, ensure_ascii=False).encode("utf-8") + b"\n" for m in messages)


def _sampling_answer(request_id: str, text: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"role": "assistant", "content": {"type": "text", "text": text}, "model": "modelo-do-host"},
    }


def test_serve_uses_mcp_sampling_when_the_host_offers_it() -> None:
    raw = _frames(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {"sampling": {}}},
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "write", "arguments": {"prompt": "Oi"}},
        },
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},  # chega enquanto o servidor espera o sampling
        _sampling_answer("jev-claud-1", "  Olá da sessão  "),
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "status", "arguments": {}}},
    )
    out = io.BytesIO()
    assert serve(io.BytesIO(raw), out, Server(lambda: JevClaud(store=":memory:"))) == 0
    sent = [json.loads(line) for line in out.getvalue().decode("utf-8").splitlines()]
    request = sent[1]
    assert request["method"] == "sampling/createMessage" and request["id"] == "jev-claud-1"
    assert request["params"]["messages"][0]["content"] == {"type": "text", "text": "Oi"}
    assert request["params"]["maxTokens"] == 800 and request["params"]["includeContext"] == "none"
    assert [m.get("id") for m in sent[2:]] == [2, 3, 4]
    assert sent[2]["result"]["content"][0]["text"] == "Olá da sessão"
    llm = json.loads(sent[4]["result"]["content"][0]["text"])["llm"]
    assert llm["mode"] == "session_sampling" and llm["model"] == "modelo-do-host"


def test_sampling_refused_by_the_host_becomes_a_tool_error() -> None:
    raw = _frames(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {"sampling": {}}},
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "write", "arguments": {"prompt": "Oi"}},
        },
        {"jsonrpc": "2.0", "id": "jev-claud-1", "error": {"code": -1, "message": "usuário recusou"}},
    )
    out = io.BytesIO()
    serve(io.BytesIO(raw), out, Server(lambda: JevClaud(store=":memory:")))
    result = json.loads(out.getvalue().decode("utf-8").splitlines()[-1])["result"]
    assert result["isError"] is True and "usuário recusou" in result["content"][0]["text"]


def test_sampling_without_answer_before_eof_is_an_error() -> None:
    raw = _frames(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {"sampling": {}}},
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "write", "arguments": {"prompt": "Oi"}},
        },
    )
    out = io.BytesIO()
    serve(io.BytesIO(raw), out, Server(lambda: JevClaud(store=":memory:")))
    result = json.loads(out.getvalue().decode("utf-8").splitlines()[-1])["result"]
    assert result["isError"] is True and "fechou a conexão" in result["content"][0]["text"]
