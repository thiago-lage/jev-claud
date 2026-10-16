"""Texto para pessoas lerem: LLMs plugáveis, priorizando opções gratuitas.

- `OpenAICompatibleWriter`: qualquer endpoint `/chat/completions` (Ollama, LM Studio,
  llama.cpp, vLLM, ou provedores com plano gratuito que expõem esse formato).
- `OllamaWriter`: atalho para o Ollama local (grátis, roda na sua máquina).
- `CallableWriter`: embrulha qualquer função `(prompt, system) -> str` (ex.: o SDK da Anthropic).

Só a biblioteca padrão é usada: nenhuma dependência extra.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from jev_claud.envvars import getenv
from jev_claud.errors import WriterError

logger = logging.getLogger("jev_claud")

OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2"


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


class OllamaWriter(OpenAICompatibleWriter):
    """Ollama local via endpoint compatível com OpenAI. Custo zero."""

    name = "ollama"

    def __init__(self, model: str | None = None, *, host: str | None = None, timeout: float = 300.0) -> None:
        self.host = ollama_host(host)
        super().__init__(
            f"{self.host}/v1",
            model or getenv("JEV_CLAUD_OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL,
            timeout=timeout,
        )

    @staticmethod
    def available(host: str | None = None, timeout: float = 0.3) -> bool:
        return OllamaWriter.models(host, timeout) is not None

    @staticmethod
    def models(host: str | None = None, timeout: float = 0.3) -> list[str] | None:
        """Modelos baixados no Ollama, ou `None` se o servidor não responde."""
        try:
            with urllib.request.urlopen(f"{ollama_host(host)}/api/tags", timeout=timeout) as response:
                if response.status != 200:
                    return None
                data = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError):
            return None
        entries = data.get("models") if isinstance(data, dict) else None
        return [str(m.get("name", "")) for m in entries or [] if isinstance(m, dict)]


def ollama_host(host: str | None = None) -> str:
    """URL base do Ollama: argumento, `OLLAMA_HOST` ou `http://localhost:11434`."""
    base = (host or os.environ.get("OLLAMA_HOST") or OLLAMA_URL).rstrip("/")
    return base if base.startswith(("http://", "https://")) else f"http://{base}"


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


def auto_writer(ollama_model: str | None = None, *, local_only: bool = False) -> Writer | None:
    """Detecta uma LLM de texto sem custo extra de configuração.

    1. `JEV_CLAUD_LLM_BASE_URL` + `JEV_CLAUD_LLM_MODEL` (+ `JEV_CLAUD_LLM_API_KEY` opcional)
    2. Ollama rodando localmente (modelo: `ollama_model`, `JEV_CLAUD_OLLAMA_MODEL` ou `llama3.2`)
    3. nenhuma (o `write()` explica como configurar)

    Com `local_only` (modo offline), endpoints fora da máquina ou da rede local são ignorados.
    """
    base_url = getenv("JEV_CLAUD_LLM_BASE_URL").strip()
    if base_url and local_only and not is_local_url(base_url):
        logger.warning("Modo offline: ignorando JEV_CLAUD_LLM_BASE_URL remoto (%s).", base_url)
        base_url = ""
    if base_url:
        model = getenv("JEV_CLAUD_LLM_MODEL").strip()
        if not model:
            raise WriterError("Defina JEV_CLAUD_LLM_MODEL junto com JEV_CLAUD_LLM_BASE_URL.")
        return OpenAICompatibleWriter(base_url, model, api_key=getenv("JEV_CLAUD_LLM_API_KEY") or None)
    if local_only and not is_local_url(ollama_host()):
        logger.warning("Modo offline: ignorando OLLAMA_HOST remoto (%s).", ollama_host())
        return None
    if OllamaWriter.available():
        return OllamaWriter(ollama_model)
    return None


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
