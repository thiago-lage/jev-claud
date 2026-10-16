"""Servidor MCP (stdio): o Claude delega trabalho pesado ao Jev e à LLM local.

Para que serve: economizar tokens do Claude. Os dados volumosos nunca entram no contexto
dele: o Claude manda uma instrução curta (ex.: "classifique `tickets.csv`"), o jev-claud
processa localmente e devolve só o resultado compacto (contagens, rótulos, um resumo).

Ferramentas: `decide`, `classify`, `rank`, `summarize_file`, `write`, `status`.

Registro no Claude Code (o `jev-claud setup` faz isto):

    claude mcp add -s user jev-claud -- python -m jev_claud mcp

Implementação mínima do protocolo (JSON-RPC 2.0, uma mensagem por linha), sem dependências.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, BinaryIO

from jev_claud import __version__, savings
from jev_claud.core import JevClaud
from jev_claud.decision import Band, Region
from jev_claud.effort import EFFORTS, WRITE_TIER, normalize_effort
from jev_claud.errors import JevClaudError
from jev_claud.media import Image
from jev_claud.questions import Question, box, choice, noul, score

MAX_ITEMS = 500
MAX_FILE_CHARS = 400_000
FALLBACK_PROTOCOL = "2025-06-18"

INSTRUCTIONS = """\
Ferramentas para economizar tokens: o trabalho pesado roda fora do contexto do Claude.
- Muitos itens para classificar, filtrar ou ranquear, ou um arquivo de dados grande para resumir:
  chame `classify`, `rank` ou `summarize_file` passando o CAMINHO do arquivo, em vez de lê-lo.
- Decisão tipada que o código vai ramificar: `decide` (TypeSafe Jev; offline grátis sem orçamento).
- Texto em prosa longo ou repetitivo em que um modelo pequeno basta: `write` (Ollama local).
  Revise antes de mostrar ao usuário: a LLM local é bem menos capaz que o Claude.
- `effort` (baixo, medio, alto) em `summarize_file` e `write` escolhe quanto trabalho e qual
  modelo local usar: baixo para triagem rápida, alto quando a fidelidade importa. Tudo grátis.
Nunca use estas ferramentas para escrever, gerar ou editar código: implemente você mesmo,
editando os arquivos. Para uma pergunta pequena e única, responda direto."""

_EFFORT = {
    "type": "string",
    "enum": list(EFFORTS),
    "default": "medio",
    "description": (
        "Quanto trabalho a LLM local faz: baixo (blocos grandes, modelo rápido), medio (padrão) ou "
        "alto (blocos menores com sobreposição, modelo forte na junção). Sempre grátis e local."
    ),
}

_QUESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["choice", "noul", "score", "box"]},
        "instructions": {"type": "string", "description": "A pergunta, completa (o nome não é enviado)."},
        "options": {
            "type": "object",
            "description": "choice: rótulo -> descrição com os termos típicos daquela opção.",
            "additionalProperties": {"type": ["string", "null"]},
        },
        "levels": {
            "type": "array",
            "items": {"type": "string"},
            "description": "score: 2 a 10 níveis, do menor ao maior.",
        },
        "yes": {"type": "string", "description": "noul: o que caracteriza o 'sim' (opcional)."},
        "no": {"type": "string", "description": "noul: o que caracteriza o 'não' (opcional)."},
    },
    "required": ["type", "instructions"],
}

_SOURCE = {
    "file": {"type": "string", "description": "Caminho de um arquivo de texto local (uma linha por item)."},
    "items": {"type": "array", "items": {"type": "string"}, "description": "Itens, se forem poucos."},
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "decide",
        "description": (
            "Decisões tipadas (choice/noul/score/box) sobre um texto, objeto ou imagem, para o código "
            "ramificar. Várias perguntas independentes numa chamada. Cascata: o óbvio sai grátis "
            "(offline), só o incerto vai ao TypeSafe Jev quando há orçamento (confira `source`). "
            "Imagens e `box` (onde algo está na imagem) exigem o Jev."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {"description": "Texto ou JSON a avaliar.", "type": ["string", "object", "array"]},
                "file": {"type": "string", "description": "Ou: caminho de um arquivo lido localmente."},
                "images": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Caminhos de imagens (PNG/JPEG) a avaliar junto com o estado.",
                },
                "questions": {
                    "type": "object",
                    "description": "nome -> pergunta.",
                    "additionalProperties": _QUESTION_SCHEMA,
                },
            },
            "required": ["questions"],
        },
    },
    {
        "name": "classify",
        "description": (
            "Classifica MUITOS itens (linhas de um arquivo ou lista) em rótulos, localmente. Devolve "
            "contagens e os itens incertos, sem colocar os dados no seu contexto. Ideal para logs, "
            "tickets, CSVs e listas grandes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **_SOURCE,
                "instructions": {"type": "string", "description": "O que decidir sobre cada item."},
                "options": {
                    "type": "object",
                    "description": "rótulo -> descrição com termos típicos.",
                    "additionalProperties": {"type": ["string", "null"]},
                },
                "return_items": {
                    "type": "boolean",
                    "description": "Inclui o rótulo de cada item (padrão: só se forem até 50).",
                },
            },
            "required": ["instructions", "options"],
        },
    },
    {
        "name": "rank",
        "description": (
            "Ordena itens (linhas de um arquivo ou lista) por relevância para uma consulta e devolve só os "
            "top_k. Útil para escolher quais trechos ou arquivos valem a pena ler."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **_SOURCE,
                "query": {"type": "string"},
                "top_k": {"type": "integer", "minimum": 1, "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "summarize_file",
        "description": (
            "Resume um arquivo de texto grande com a LLM local (Ollama, grátis), em blocos, e devolve só o "
            "resumo. Use antes de decidir se precisa ler o arquivo inteiro. Modelo pequeno: confira o "
            "essencial no original se a resposta for crítica."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "instructions": {
                    "type": "string",
                    "description": "Foco do resumo (padrão: pontos principais em tópicos).",
                },
                "max_chars": {"type": "integer", "minimum": 1000, "default": 60000},
                "effort": _EFFORT,
            },
            "required": ["path"],
        },
    },
    {
        "name": "write",
        "description": (
            "Gera prosa com a LLM local (Ollama, grátis): rascunhos, variações, traduções simples, textos "
            "repetitivos. Nunca para código. Revise o resultado; para textos importantes, escreva você mesmo."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "context": {
                    "description": "Dados de apoio (texto ou JSON).",
                    "type": ["string", "object", "array"],
                },
                "system": {"type": "string"},
                "max_tokens": {"type": "integer", "minimum": 16, "default": 800},
                "effort": _EFFORT,
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "status",
        "description": (
            "Mostra o que está ativo: modo offline, Jev (orçamento, gasto do mês), LLM local e os "
            "modelos de cada esforço, e o cache."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class ToolError(Exception):
    """Erro de uso de ferramenta: vira `isError: true` para o Claude corrigir a chamada."""


class Server:
    def __init__(self, app_factory: Callable[[], JevClaud] = JevClaud) -> None:
        self._factory = app_factory
        self._app: JevClaud | None = None

    @property
    def app(self) -> JevClaud:
        if self._app is None:
            self._app = self._factory()
        return self._app

    def close(self) -> None:
        if self._app is not None:
            self._app.close()

    # -- protocolo -------------------------------------------------------------------

    def handle(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
            return _error(
                message.get("id") if isinstance(message, dict) else None, -32600, "Requisição inválida"
            )
        method, params, msg_id = message["method"], message.get("params") or {}, message.get("id")
        is_notification = "id" not in message
        try:
            if method == "initialize":
                result: Any = self._initialize(params)
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = self._call(params)
            elif is_notification:
                return None  # notifications/initialized, notifications/cancelled, ...
            else:
                return _error(msg_id, -32601, f"Método não suportado: {method}")
        except Exception as error:  # nunca derrubar o servidor por uma chamada
            return None if is_notification else _error(msg_id, -32603, f"Erro interno: {error}")
        return None if is_notification else {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _initialize(self, params: Mapping[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        return {
            "protocolVersion": requested if isinstance(requested, str) else FALLBACK_PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "jev-claud", "version": __version__},
            "instructions": INSTRUCTIONS,
        }

    def _call(self, params: Mapping[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = {
            "decide": self.decide,
            "classify": self.classify,
            "rank": self.rank,
            "summarize_file": self.summarize_file,
            "write": self.write,
            "status": self.status,
        }.get(str(name))
        if handler is None:
            return _text(f"Ferramenta desconhecida: {name}", error=True)
        if not isinstance(arguments, dict):
            return _text("`arguments` deve ser um objeto.", error=True)
        try:
            output = handler(arguments)
        except (ToolError, JevClaudError, ValueError, OSError) as error:
            return _text(str(error), error=True)
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        self._record_saving(str(name), arguments, len(text))
        return _text(text)

    def _record_saving(self, name: str, args: Mapping[str, Any], answer_chars: int) -> None:
        """O Claude recebeu `answer_chars` em vez de ler o arquivo inteiro."""
        source = args.get("path") if name == "summarize_file" else args.get("file")
        if not source:
            return
        try:
            size = Path(str(source)).expanduser().stat().st_size
        except OSError:
            return
        limit = int(args.get("max_chars") or 60_000) if name == "summarize_file" else MAX_FILE_CHARS
        savings.record(savings.TOOLS, min(size, limit) - answer_chars, self.app.store)

    # -- ferramentas -------------------------------------------------------------------

    def decide(self, args: Mapping[str, Any]) -> dict[str, Any]:
        state = _read_file(args["file"], MAX_FILE_CHARS) if args.get("file") else args.get("state")
        images = args.get("images") or []
        if not isinstance(images, list):
            raise ToolError("`images` deve ser uma lista de caminhos.")
        if images:
            loaded = [Image(Path(str(path)).expanduser()) for path in images]
            pictures: Any = loaded[0] if len(loaded) == 1 else loaded
            state = {"texto": state, "imagens": pictures} if state not in (None, "") else pictures
        if state in (None, ""):
            raise ToolError("Informe `state`, `file` ou `images`.")
        raw = args.get("questions")
        if not isinstance(raw, dict) or not raw:
            raise ToolError("`questions` deve ser um objeto nome -> pergunta.")
        decisions = self.app.decide(state, {name: _question(name, q) for name, q in raw.items()})
        result: dict[str, Any] = {
            "source": decisions.source,
            "cost_usd": round(decisions.cost_usd, 8),
            "decisions": {name: _compact(d) for name, d in decisions.items()},
        }
        if decisions.escalated:
            result["escalated"] = list(decisions.escalated)
        return result

    def classify(self, args: Mapping[str, Any]) -> dict[str, Any]:
        items = _items(args)
        options = args.get("options")
        if not isinstance(options, dict) or not options:
            raise ToolError("`options` deve mapear rótulo -> descrição.")
        question = choice(str(args.get("instructions", "")), options)
        counts: Counter[str] = Counter()
        uncertain: list[dict[str, Any]] = []
        labelled: list[dict[str, Any]] = []
        sources: Counter[str] = Counter()
        cost = 0.0
        for index, item in enumerate(items):
            result = self.app.decide(item, {"label": question})
            decision = result["label"]
            counts[str(decision.value)] += 1
            sources[result.source] += 1
            cost += result.cost_usd
            entry = {"i": index, "label": decision.value, "confidence": round(decision.confidence, 2)}
            labelled.append(entry)
            if decision.band is Band.LOW:
                uncertain.append({**entry, "text": _clip(item, 160)})
        show_all = args.get("return_items", len(items) <= 50)
        return {
            "total": len(items),
            "counts": dict(counts.most_common()),
            "uncertain": uncertain[:20],
            "uncertain_total": len(uncertain),
            "items": labelled if show_all else None,
            "sources": dict(sources),
            "cost_usd": round(cost, 8),
        }

    def rank(self, args: Mapping[str, Any]) -> dict[str, Any]:
        items = _items(args)
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolError("Informe `query`.")
        top_k = int(args.get("top_k") or 5)
        ranked = self.app.rank(query, items, top_k=top_k)
        return {
            "total": len(items),
            "top": [
                {"i": hit.index, "score": round(hit.score, 2), "text": _clip(str(hit.item), 300)}
                for hit in ranked
            ],
            "source": ranked[0].decision.source if ranked else None,
        }

    def summarize_file(self, args: Mapping[str, Any]) -> str:
        effort = normalize_effort(args.get("effort"))
        text = _read_file(str(args.get("path") or ""), int(args.get("max_chars") or 60_000))
        if not text.strip():
            raise ToolError("O arquivo está vazio: nada para resumir.")
        return self.app.summarize(text, focus=args.get("instructions") or None, effort=effort)

    def write(self, args: Mapping[str, Any]) -> str:
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise ToolError("Informe `prompt`.")
        return self.app.write(
            prompt,
            context=args.get("context"),
            system=args.get("system"),
            max_tokens=int(args.get("max_tokens") or 800),
            tier=WRITE_TIER[normalize_effort(args.get("effort"))],
        )

    def status(self, args: Mapping[str, Any]) -> dict[str, Any]:
        status = self.app.status()
        writer = self.app.writer
        status["writer"] = f"{writer.name} {getattr(writer, 'model', '')}".strip() if writer else None
        if writer is not None:
            status["effort_models"] = self.app.tier_models()
        return status


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------


def _question(name: str, spec: Any) -> Question:
    if not isinstance(spec, dict):
        raise ToolError(f"Pergunta {name!r} deve ser um objeto.")
    kind, instructions = spec.get("type"), spec.get("instructions")
    if not isinstance(instructions, (str, dict, list)) or not instructions:
        raise ToolError(f"Pergunta {name!r}: `instructions` é obrigatório.")
    if kind == "choice":
        return choice(instructions, spec.get("options") or {})
    if kind == "noul":
        return noul(instructions, yes=spec.get("yes"), no=spec.get("no"))
    if kind == "score":
        return score(instructions, spec.get("levels") or [])
    if kind == "box":
        return box(instructions)
    raise ToolError(f"Pergunta {name!r}: `type` deve ser choice, noul, score ou box.")


def _compact(decision: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "value": decision.value.as_dict() if isinstance(decision.value, Region) else decision.value,
        "confidence": round(decision.confidence, 3),
        "band": decision.band.value,
    }
    if decision.kind == "noul":
        out["p_yes"] = round(decision.probability, 3)
    if decision.kind == "score":
        out["level"] = decision.level
    return out


def _items(args: Mapping[str, Any]) -> list[str]:
    if args.get("file"):
        lines = _read_file(args["file"], MAX_FILE_CHARS).splitlines()
        items = [line.strip() for line in lines if line.strip()]
    else:
        raw = args.get("items")
        if not isinstance(raw, list):
            raise ToolError("Informe `file` ou `items`.")
        items = [str(item) for item in raw if str(item).strip()]
    if not items:
        raise ToolError("Nenhum item para processar.")
    if len(items) > MAX_ITEMS:
        raise ToolError(
            f"Máximo de {MAX_ITEMS} itens por chamada (recebidos {len(items)}). Divida em partes."
        )
    return items


def _read_file(path: str, max_chars: int) -> str:
    file = Path(str(path)).expanduser()
    if not file.is_file():
        raise ToolError(f"Arquivo não encontrado: {file}")
    text = file.read_text(encoding="utf-8", errors="replace")
    return text[:max_chars]


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _text(text: str, *, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def serve(stdin: BinaryIO | None = None, stdout: BinaryIO | None = None, server: Server | None = None) -> int:
    """Loop stdio: lê uma mensagem JSON por linha e responde na mesma ordem."""
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    server = server or Server()
    try:
        for raw in stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                response: dict[str, Any] | None = _error(None, -32700, "JSON inválido")
            else:
                response = server.handle(message)
            if response is not None:
                stdout.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
                stdout.flush()
    finally:
        server.close()
    return 0
