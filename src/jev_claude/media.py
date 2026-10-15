"""Imagens para o System One (modelos de visão do Jev), sem depender do SDK no núcleo.

Use uma `Image` como estado, dentro de um dicionário/lista, ou dentro de um texto
(f-string): `str(imagem)` é uma referência estável, derivada do conteúdo (sha-256).
Na hora de chamar o Jev, o backend troca cada referência pelo asset do `typesafe-sdk`.

O modo offline não enxerga imagens: perguntas sobre imagens sempre vão para o Jev na
cascata, e sem o Jev elas falham com uma mensagem clara.
"""

from __future__ import annotations

import hashlib
import json
import re
import weakref
from collections.abc import Iterator, Mapping
from os import PathLike
from pathlib import Path
from typing import Any

from jev_claude.errors import QuestionError

REFERENCE = re.compile(r"<image:[0-9a-f]{32}>")
# Tokens de entrada cobrados por imagem não são publicados; a estimativa é conservadora.
IMAGE_TOKENS = 1500

_SIGNATURES = {b"\x89PNG\r\n\x1a\n": "image/png", b"\xff\xd8\xff": "image/jpeg", b"RIFF": "image/webp"}
_REGISTRY: weakref.WeakValueDictionary[str, Image] = weakref.WeakValueDictionary()


class Image:
    """Uma imagem a partir de um caminho de arquivo ou de bytes.

    >>> foto = Image("produto.jpg")                     # doctest: +SKIP
    >>> r.when(foto, "O produto chegou danificado?")    # doctest: +SKIP
    >>> r.decide({"foto": foto, "pedido": "#123"}, {...})  # doctest: +SKIP
    """

    __slots__ = ("__weakref__", "_data", "_digest", "_path")

    def __init__(self, source: str | PathLike[str] | bytes | bytearray | memoryview) -> None:
        if isinstance(source, (bytes, bytearray, memoryview)):
            data = bytes(source)
            if not data:
                raise QuestionError("Image recebeu bytes vazios.")
            self._data: bytes | None = data
            self._path: Path | None = None
        else:
            path = Path(source)
            if not path.is_file():
                raise QuestionError(f"Imagem não encontrada: {path}")
            self._data = None
            self._path = path
        self._digest = hashlib.sha256(self.data).hexdigest()
        _REGISTRY[self.reference] = self

    @property
    def data(self) -> bytes:
        if self._data is None:
            assert self._path is not None
            self._data = self._path.read_bytes()
        return self._data

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def reference(self) -> str:
        """Estável para o mesmo conteúdo: o cache funciona entre execuções."""
        return f"<image:{self._digest[:32]}>"

    @property
    def content_type(self) -> str:
        head = self.data[:8]
        return next((kind for sig, kind in _SIGNATURES.items() if head.startswith(sig)), "image/*")

    def __str__(self) -> str:
        return self.reference

    def __repr__(self) -> str:
        where = str(self._path) if self._path else f"{len(self.data)} bytes"
        return f"Image({where})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Image) and other._digest == self._digest

    def __hash__(self) -> int:
        return hash(self._digest)


def lookup(reference: str) -> Image | None:
    """A `Image` viva com essa referência (usada dentro de textos), se houver."""
    return _REGISTRY.get(reference)


def replace_images(content: Any) -> Any:
    """Troca cada `Image` por sua referência textual (estrutura nova; a original fica intacta)."""
    if isinstance(content, Image):
        return content.reference
    if isinstance(content, Mapping):
        return {key: replace_images(value) for key, value in content.items()}
    if isinstance(content, (list, tuple)):
        return [replace_images(value) for value in content]
    return content


def references(*contents: Any) -> list[str]:
    """Referências de imagem presentes (como objeto ou dentro de textos), sem repetir."""
    found: dict[str, None] = {}
    for content in contents:
        for text in _strings(replace_images(content)):
            for match in REFERENCE.findall(text):
                found[match] = None
    return list(found)


def has_images(*contents: Any) -> bool:
    return bool(references(*contents))


def collect(*contents: Any) -> dict[str, Image]:
    """As `Image` presentes como objeto (referência forte: não somem antes do envio)."""
    found: dict[str, Image] = {}

    def walk(content: Any) -> None:
        if isinstance(content, Image):
            found[content.reference] = content
        elif isinstance(content, Mapping):
            for value in content.values():
                walk(value)
        elif isinstance(content, (list, tuple)):
            for value in content:
                walk(value)

    for content in contents:
        walk(content)
    return found


def resolve(refs: list[str], known: Mapping[str, Image] | None = None) -> dict[str, Image]:
    """Referência -> `Image`. Falha se o texto citar uma imagem que não existe mais."""
    resolved: dict[str, Image] = {}
    for ref in refs:
        image = (known or {}).get(ref) or lookup(ref)
        if image is None:
            raise QuestionError(
                f"Referência {ref} sem a Image correspondente (crie a Image antes de usá-la)."
            )
        resolved[ref] = image
    return resolved


def _strings(content: Any) -> Iterator[str]:
    if isinstance(content, str):
        yield content
    elif isinstance(content, Mapping):
        for key, value in content.items():
            yield str(key)
            yield from _strings(value)
    elif isinstance(content, (list, tuple)):
        for value in content:
            yield from _strings(value)
    elif content is not None and not isinstance(content, (int, float, bool)):
        yield json.dumps(content, default=str, ensure_ascii=False)
