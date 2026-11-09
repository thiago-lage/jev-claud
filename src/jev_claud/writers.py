"""Texto para pessoas lerem: o LLM da sessão por padrão, outros só quando você passa.

O jev-claud opera dentro do LLM da sessão em que está instalado: no servidor MCP, o texto
vai ao host por MCP sampling (`jev_claud.mcp.SamplingWriter`) ou volta ao agente em blocos.
Nada é detectado nem chamado fora da sessão por conta própria.

Para usar a biblioteca em um app, passe um `Writer` explicitamente a `JevClaud(writer=...)`:

- `CallableWriter`: embrulha qualquer função `(prompt, system) -> str` (ex.: o SDK da Anthropic).
- `OpenAICompatibleWriter`: um endpoint `/chat/completions` que você escolheu.

Só a biblioteca padrão é usada: nenhuma dependência extra.
"""

from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from jev_claud.errors import WriterError


@runtime_checkable
class Writer(Protocol):
    name: str

    def write(
        self, prompt: str, *, system: str | None = None, max_tokens: int = 800, temperature: float = 0.3
    ) -> str: ...


class OpenAICompatibleWriter:
    """Cliente mínimo para APIs no formato OpenAI Chat Completions."""

    name = "openai-compatible"

    def __init__(
        self, base_url: str, model: str, *, api_key: str | None = None, timeout: float = 120.0
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self.timeout = timeout

    def write(
        self, prompt: str, *, system: str | None = None, max_tokens: int = 800, temperature: float = 0.3
    ) -> str:
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        data = _post_json(f"{self.base_url}/chat/completions", body, headers, self.timeout)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise WriterError(f"Resposta inesperada da LLM: {str(data)[:300]}") from error
        return str(content).strip()


class CallableWriter:
    """Usa qualquer função como LLM: `fn(prompt, system) -> str`."""

    name = "callable"

    def __init__(self, fn: Callable[[str, str | None], str], *, name: str = "callable") -> None:
        self._fn = fn
        self.name = name

    def write(
        self, prompt: str, *, system: str | None = None, max_tokens: int = 800, temperature: float = 0.3
    ) -> str:
        return self._fn(prompt, system)


def is_local_url(url: str) -> bool:
    """`True` se `url` aponta para esta máquina ou para a rede local (nada vai à internet)."""
    host = (urllib.parse.urlsplit(url if "//" in url else f"http://{url}").hostname or "").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local or address.is_unspecified


def _post_json(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float) -> Any:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        raise WriterError(f"LLM respondeu HTTP {error.code}: {detail}") from error
    except (OSError, ValueError) as error:
        raise WriterError(f"Falha ao chamar a LLM em {url}: {error}") from error
