"""Backend TypeSafe Jev (System One) — decisões tipadas e calibradas, pagas por token de entrada."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from jev_claude.backends.base import RawAnswer, RawResult
from jev_claude.errors import BackendError
from jev_claude.questions import Question, to_wire

DEFAULT_MODEL = "jev-latest"


@dataclass(frozen=True)
class ModelInfo:
    name: str
    release_date: str
    description: str


class JevBackend:
    """Envia perguntas ao endpoint System One do TypeSafe via `typesafe-sdk`.

    Passe `client` para injetar um cliente pronto (útil em testes); caso contrário, um
    `typesafe_sdk.TypeSafeClient` é criado sob demanda lendo `TYPESAFE_API_KEY`.
    """

    name = "jev"
    paid = True

    def __init__(
        self,
        *,
        client: Any | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._timeout = timeout
        self._headers = dict(headers) if headers else None

    @staticmethod
    def sdk_available() -> bool:
        try:
            import typesafe_sdk  # noqa: F401
        except ImportError:
            return False
        return True

    @staticmethod
    def sdk_version() -> str | None:
        try:
            from importlib.metadata import version

            return version("typesafe-sdk")
        except Exception:
            return None

    @staticmethod
    def key_configured(api_key: str | None = None) -> bool:
        return bool((api_key or os.environ.get("TYPESAFE_API_KEY") or "").strip())

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from typesafe_sdk import TypeSafeClient
            except ImportError as error:
                raise BackendError("Instale o SDK do Jev: pip install 'jev-claude[jev]'") from error
            kwargs: dict[str, Any] = {}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            if self._timeout is not None:
                kwargs["timeout"] = self._timeout
            if self._headers:
                kwargs["headers"] = self._headers
            try:
                self._client = TypeSafeClient(**kwargs)
            except Exception as error:  # chave inválida é rejeitada já na criação
                raise BackendError(f"Não foi possível criar o cliente TypeSafe: {error}") from error
        return self._client

    def evaluate(self, state: Any, questions: Mapping[str, Question], *, model: str | None) -> RawResult:
        try:
            response = self.client.system_one(state=state, questions=to_wire(questions), model=model)
        except BackendError:
            raise
        except Exception as error:
            raise BackendError(f"Jev falhou: {error}") from error
        answers = {name: _convert(answer) for name, answer in response.answers.items()}
        usage = getattr(response, "usage", None)
        return RawResult(
            answers=answers,
            model=getattr(response, "model", None),
            input_tokens=getattr(usage, "input_tokens", None),
        )

    def list_models(self) -> list[ModelInfo]:
        try:
            listing = self.client.models.list()
        except BackendError:
            raise
        except Exception as error:
            raise BackendError(f"Não foi possível listar os modelos do Jev: {error}") from error
        return [ModelInfo(m.name, m.release_date, m.description) for m in listing.models]

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()


def _convert(answer: Any) -> RawAnswer:
    kind = getattr(answer, "type", None)
    if kind == "noul":
        return RawAnswer(kind="noul", value=float(answer.noul))
    if kind == "choice":
        return RawAnswer(
            kind="choice",
            value=str(answer.choice),
            probabilities={str(k): float(v) for k, v in answer.probabilities.items()},
            confidence=float(answer.confidence),
        )
    if kind == "score":
        return RawAnswer(
            kind="score",
            value=float(answer.score),
            probabilities={str(k): float(v) for k, v in answer.probabilities.items()},
            confidence=float(answer.confidence),
            legend={str(k): v for k, v in answer.legend.items()},
        )
    raise BackendError(f"Tipo de resposta não suportado pelo jev-claude: {kind!r}")
