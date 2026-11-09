from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from jev_claud import JevClaud, NoWriterError, OpenAICompatibleWriter, WriterError


class _Handler(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []  # noqa: RUF012
    status = 200

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append(
            {"path": self.path, "body": body, "auth": self.headers.get("Authorization")}
        )
        if type(self).status != 200:
            self._send(type(self).status, {"error": "quota"})
            return
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": "  Olá, tudo certo!  "}}]})

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    _Handler.requests = []
    _Handler.status = 200
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_openai_compatible_writer(server: str) -> None:
    writer = OpenAICompatibleWriter(f"{server}/v1/", "modelo-x", api_key="chave")
    assert writer.write("Diga oi", system="Seja breve.", max_tokens=50, temperature=0.1) == "Olá, tudo certo!"
    request = _Handler.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["auth"] == "Bearer chave"
    assert request["body"] == {
        "model": "modelo-x",
        "messages": [{"role": "system", "content": "Seja breve."}, {"role": "user", "content": "Diga oi"}],
        "max_tokens": 50,
        "temperature": 0.1,
    }


def test_http_error_becomes_writer_error(server: str) -> None:
    _Handler.status = 429
    with pytest.raises(WriterError, match="429"):
        OpenAICompatibleWriter(server, "m").write("oi")


def test_unreachable_endpoint_becomes_writer_error() -> None:
    with pytest.raises(WriterError):
        OpenAICompatibleWriter("http://127.0.0.1:9", "m", timeout=1).write("oi")


def test_no_writer_is_detected_from_the_environment(monkeypatch: pytest.MonkeyPatch, server: str) -> None:
    """Sem `Writer` explícito, nada é detectado: o texto fica com o LLM da sessão."""
    monkeypatch.setenv("JEV_CLAUD_LLM_BASE_URL", f"{server}/v1")
    monkeypatch.setenv("JEV_CLAUD_LLM_MODEL", "qualquer")
    with JevClaud(store=":memory:") as r:
        assert r.writer is None
        with pytest.raises(NoWriterError, match="LLM de sessão"):
            r.write("oi")
    assert _Handler.requests == []
