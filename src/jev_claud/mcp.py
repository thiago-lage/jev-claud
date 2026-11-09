"""Servidor MCP (stdio): o jev-claud opera dentro do LLM da sessão em que está instalado.

Para que serve: economizar tokens do Claude. O trabalho determinístico (ler, quebrar em
blocos, contar, deduplicar, cache, cascata offline) roda aqui; o que precisa de linguagem
vai ao LLM da sessão, sem backend externo nem modelo local:

- Host com **MCP sampling** (declara `sampling` no `initialize`): o servidor pede o texto
  ao host com `sampling/createMessage` e devolve o resultado pronto.
- Host **sem sampling** (o Claude Code, hoje): as ferramentas devolvem o trabalho ao agente
  (`mode: "agent"`): os blocos a resumir, os itens incertos, a tarefa de escrita.

Ferramentas: `decide`, `classify`, `rank`, `summarize_file`, `write`, `status`.

Registro no Claude Code (o `jev-claud setup` faz isto):

    claude mcp add -s user jev-claud -- python -m jev_claud mcp

Implementação mínima do protocolo (JSON-RPC 2.0, uma mensagem por linha), sem dependências.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import sys
import time
from collections import Counter, OrderedDict, deque
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any, BinaryIO

from jev_claud import __version__, savings
from jev_claud.core import DEFAULT_FOCUS, JevClaud, summary_blocks
from jev_claud.decision import Band, Region
from jev_claud.effort import EFFORTS, WRITE_TOKENS, effort_plan, normalize_effort
from jev_claud.errors import JevClaudError, WriterError
from jev_claud.evidence import diagnose_log, read_evidence, read_source, search_file
from jev_claud.media import Image
from jev_claud.questions import Question, box, choice, noul, score

MAX_ITEMS = 500
MAX_FILE_CHARS = 400_000
FALLBACK_PROTOCOL = "2025-06-18"
AGENT_PAGE_CHARS = 24_000
"""Quanto texto de blocos uma resposta no modo agente traz, no máximo (fora o primeiro bloco)."""
SESSION_BATCH = 25
"""Itens por pedido ao LLM da sessão em `classify`."""
SESSION_MAX_ITEMS = 200
"""Teto de itens incertos que `classify` leva ao LLM da sessão numa chamada."""

INSTRUCTIONS = """\
Ferramentas para economizar tokens: o trabalho determinístico roda aqui; o que precisa de
linguagem fica com o LLM desta sessão (por MCP sampling, quando o host oferece; senão, com você).
- O agente principal planeja, edita e valida. Para logs de testes/builds, use `diagnose_log`:
  sinais determinísticos com linhas, sem inventar causa raiz. `search_file` busca texto literal;
  `read_evidence` amplia o trecho. Confira coverage e needs_agent_review antes de concluir.
- Muitos itens para classificar, filtrar ou ranquear, ou um arquivo grande para resumir:
  chame `classify`, `rank` ou `summarize_file` passando o CAMINHO do arquivo, em vez de lê-lo.
- Decisão tipada que o código vai ramificar: `decide` (cascata offline grátis; o TypeSafe Jev só
  com chave e orçamento; o incerto vai ao LLM da sessão quando há sampling).
- `mode: "agent"` na resposta: o host não oferece sampling e o trabalho voltou para você. Siga
  `instructions`: resuma os blocos e peça os próximos com `block`, classifique os incertos, escreva.
- `effort` (baixo, medio, alto, extra, max, ultracode) controla só o tamanho dos blocos e a
  profundidade da junção; ultracode acrescenta uma revisão contra os parciais. Não escolhe modelo.
  Resumos são auxiliares: confira no original.
Nunca use estas ferramentas para escrever, gerar ou editar código: implemente você mesmo,
editando os arquivos. Para uma pergunta pequena e única, responda direto."""

_EFFORT = {
    "type": "string",
    "enum": list(EFFORTS),
    "default": "medio",
    "description": (
        "Quanto trabalho fazer, como o seletor de esforço do Claude: baixo (blocos grandes, junção "
        "curta), medio (padrão), alto e extra (blocos menores, junção mais longa), max (os menores "
        "blocos) e ultracode (max + revisão contra os parciais). Controla só blocos e profundidade; "
        "o modelo é sempre o LLM da sessão. Não certifica fidelidade."
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
            "(offline), o incerto vai ao TypeSafe Jev quando há orçamento e, depois, ao LLM da sessão "
            "quando o host oferece sampling (confira `source`). Imagens e `box` exigem o Jev."
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
            "Classifica MUITOS itens (linhas de um arquivo ou lista) em rótulos. O óbvio sai offline; "
            "os incertos vão ao LLM da sessão (sampling) ou voltam para você. Devolve contagens e os "
            "incertos, sem colocar os dados no seu contexto. Ideal para logs, tickets e listas."
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
            "top_k. Com sampling, o LLM da sessão reordena os melhores candidatos. Útil para escolher "
            "quais trechos ou arquivos valem a pena ler."
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
            "Resume um arquivo de texto grande em blocos, com o LLM da sessão. Com sampling, devolve só o "
            "resumo, a cobertura e a indicação de cache. Sem sampling (mode=agent), devolve os blocos "
            "paginados para você resumir; grave o resultado com save_summary para reutilizá-lo. "
            "Resultado não verificado: confira o essencial com search_file/read_evidence."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "instructions": {
                    "type": "string",
                    "description": "Foco do resumo (padrão: pontos principais em tópicos).",
                },
                "max_chars": {"type": "integer", "minimum": 1, "maximum": 2_000_000, "default": 60000},
                "cache": {
                    "type": "boolean",
                    "default": True,
                    "description": "false refaz o resumo sem ler ou gravar no cache da sessão.",
                },
                "block": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 1,
                    "description": "mode=agent: primeiro bloco desta página (use o next_block anterior).",
                },
                "save_summary": {
                    "type": "string",
                    "description": (
                        "mode=agent: o resumo que você escreveu, para o cache da sessão (mesmos path, "
                        "instructions e effort). Nada é resumido nesta chamada."
                    ),
                },
                "effort": _EFFORT,
            },
            "required": ["path"],
        },
    },
    {
        "name": "write",
        "description": (
            "Gera prosa com o LLM da sessão (MCP sampling): rascunhos, variações, traduções simples, textos "
            "repetitivos. Nunca para código. Sem sampling (mode=agent), devolve a tarefa para você escrever."
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
                "max_tokens": {
                    "type": "integer",
                    "minimum": 16,
                    "description": "Limite da resposta (padrão: conforme o effort, de 400 a 2000).",
                },
                "effort": _EFFORT,
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "status",
        "description": (
            "Mostra o que está ativo: o LLM da sessão (sampling ou modo agente), Jev (orçamento, gasto "
            "do mês), modo offline, cache de resumos e atividade recente deste servidor MCP."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]

_FILE_PROPERTIES = {
    "path": {"type": "string", "description": "Caminho do arquivo local, tratado como dados."},
    "max_chars": {"type": "integer", "minimum": 1, "maximum": 2_000_000, "default": MAX_FILE_CHARS},
}
TOOLS.extend(
    [
        {
            "name": "diagnose_log",
            "description": (
                "Organiza sinais de falha de testes/builds sem LLM; devolve grupos, linhas originais, "
                "cobertura e pendências para o agente. Não determina causa raiz nem executa comandos."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    **_FILE_PROPERTIES,
                    "exit_code": {
                        "type": "integer",
                        "description": "Código observado na execução, se conhecido.",
                    },
                    "max_groups": {"type": "integer", "minimum": 1, "maximum": 30, "default": 12},
                },
                "required": ["path"],
            },
        },
        {
            "name": "search_file",
            "description": (
                "Busca literal em um arquivo, sem distinguir maiúsculas, retornando trechos com linhas. "
                "Não é busca semântica. Confira source.truncated e omitted_matches."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    **_FILE_PROPERTIES,
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
                },
                "required": ["path", "query"],
            },
        },
        {
            "name": "read_evidence",
            "description": (
                "Lê linhas originais para conferir ou ampliar uma evidência. Passe fingerprint de uma "
                "busca/diagnóstico anterior para rejeitar arquivo alterado."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    **_FILE_PROPERTIES,
                    "start_line": {"type": "integer", "minimum": 1, "default": 1},
                    "line_count": {"type": "integer", "minimum": 1, "maximum": 100, "default": 40},
                    "fingerprint": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    ]
)


class ToolError(Exception):
    """Erro de uso de ferramenta: vira `isError: true` para o Claude corrigir a chamada."""


class Channel:
    """Os dois sentidos do stdio: além de responder, o servidor também pede coisas ao host.

    `request` envia um pedido JSON-RPC (ex.: `sampling/createMessage`) e lê a entrada até a
    resposta com o mesmo id. Mensagens que chegam enquanto isso ficam na fila e são tratadas
    depois, na ordem em que chegaram.
    """

    def __init__(self, stdin: BinaryIO, stdout: BinaryIO) -> None:
        self._stdin = stdin
        self._stdout = stdout
        self._queued: deque[Any] = deque()
        self._ids = itertools.count(1)

    def send(self, message: Mapping[str, Any]) -> None:
        self._stdout.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        self._stdout.flush()

    def messages(self) -> Iterator[Any]:
        """Mensagens do host: primeiro as que chegaram durante um `request`, depois a entrada."""
        while True:
            if self._queued:
                yield self._queued.popleft()
                continue
            message = self._read()
            if message is _EOF:
                return
            yield message

    def request(self, method: str, params: Mapping[str, Any]) -> Any:
        request_id = f"jev-claud-{next(self._ids)}"
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)})
        while True:
            message = self._read()
            if message is _EOF:
                raise WriterError(f"O host fechou a conexão antes de responder a {method}.")
            if isinstance(message, dict) and message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    error = message["error"] if isinstance(message["error"], dict) else {}
                    raise WriterError(f"O host recusou {method}: {error.get('message', error)}")
                return message.get("result")
            self._queued.append(message)

    def _read(self) -> Any:
        for raw in self._stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                return json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return _INVALID
        return _EOF


_EOF = object()
_INVALID = object()


class SamplingWriter:
    """O LLM da sessão, via MCP sampling (`sampling/createMessage`): o host escolhe e cobra o modelo."""

    name = "sessão (MCP sampling)"

    def __init__(self, channel: Channel) -> None:
        self._channel = channel
        self.model: str | None = None
        """Modelo que o host informou na última resposta (o servidor não escolhe modelo)."""

    def write(
        self, prompt: str, *, system: str | None = None, max_tokens: int = 800, temperature: float = 0.3
    ) -> str:
        params: dict[str, Any] = {
            "messages": [{"role": "user", "content": {"type": "text", "text": prompt}}],
            "maxTokens": max_tokens,
            "temperature": temperature,
            "includeContext": "none",
        }
        if system:
            params["systemPrompt"] = system
        result = self._channel.request("sampling/createMessage", params)
        content = result.get("content") if isinstance(result, dict) else None
        blocks = content if isinstance(content, list) else [content]
        text = "".join(
            str(block.get("text", ""))
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if not text.strip():
            raise WriterError(f"O host não devolveu texto no sampling: {str(result)[:300]}")
        if isinstance(result, dict) and result.get("model"):
            self.model = str(result["model"])
        return text.strip()


class Server:
    def __init__(
        self, app_factory: Callable[[], JevClaud] = JevClaud, *, channel: Channel | None = None
    ) -> None:
        self._factory = app_factory
        self._app: JevClaud | None = None
        self._channel = channel
        self._client: dict[str, Any] = {}
        self._sampling = False
        self._summaries: OrderedDict[str, str] = OrderedDict()
        self._activity: deque[dict[str, Any]] = deque(maxlen=10)

    @property
    def app(self) -> JevClaud:
        if self._app is None:
            self._app = self._factory()
            self._attach_session_llm()
        return self._app

    @property
    def sampling(self) -> bool:
        """O host declarou MCP sampling e há um canal para pedir: o texto vai ao LLM da sessão."""
        return self._sampling and self._channel is not None

    def _attach_session_llm(self) -> None:
        if self._app is not None and self._app.writer is None and self.sampling:
            assert self._channel is not None
            self._app.writer = SamplingWriter(self._channel)

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
        capabilities = params.get("capabilities")
        self._sampling = isinstance(capabilities, dict) and isinstance(capabilities.get("sampling"), dict)
        info = params.get("clientInfo")
        self._client = (
            {key: str(info[key]) for key in ("name", "version") if key in info}
            if isinstance(info, dict)
            else {}
        )
        self._attach_session_llm()
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
            "diagnose_log": self.diagnose_log,
            "search_file": self.search_file,
            "read_evidence": self.read_evidence,
        }.get(str(name))
        if handler is None:
            return _text(f"Ferramenta desconhecida: {name}", error=True)
        if not isinstance(arguments, dict):
            return _text("`arguments` deve ser um objeto.", error=True)
        started = time.monotonic()
        try:
            output = handler(arguments)
        except (ToolError, JevClaudError, ValueError, OSError) as error:
            self._activity.append(
                {"tool": name, "status": "error", "elapsed_ms": round((time.monotonic() - started) * 1000)}
            )
            return _text(str(error), error=True)
        if name != "status":
            event = {"tool": name, "status": "ok", "elapsed_ms": round((time.monotonic() - started) * 1000)}
            if isinstance(output, dict):
                event["needs_agent_review"] = output.get("needs_agent_review", False)
                event["cached"] = output.get("cached", False)
            self._activity.append(event)
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
        compact = {name: _compact(d) for name, d in decisions.items()}
        pending = [n for n, d in decisions.items() if d.band is not Band.HIGH or d.source != "jev"]
        resolved: list[str] = []
        if pending and not images and self.app.writer is not None:
            resolved = self._session_decide(state, {n: raw[n] for n in pending}, compact)
        result: dict[str, Any] = {
            "source": decisions.source,
            "cost_usd": round(decisions.cost_usd, 8),
            "decisions": compact,
            "llm": self._llm_mode(),
        }
        if decisions.escalated:
            result["escalated"] = list(decisions.escalated)
        if resolved:
            result["session_resolved"] = resolved
        result["review_questions"] = [n for n in pending if n not in resolved]
        result["needs_agent_review"] = bool(result["review_questions"])
        if result["needs_agent_review"] and self.app.writer is None:
            result["next_action"] = (
                "Host sem MCP sampling: decida você as review_questions (o candidate é só um palpite)."
            )
        return result

    def classify(self, args: Mapping[str, Any]) -> dict[str, Any]:
        items = _items(args)
        options = args.get("options")
        if not isinstance(options, dict) or not options:
            raise ToolError("`options` deve mapear rótulo -> descrição.")
        instructions = str(args.get("instructions", ""))
        question = choice(instructions, options)
        sources: Counter[str] = Counter()
        cost = 0.0
        labelled: list[dict[str, Any]] = []
        review: list[int] = []
        for index, item in enumerate(items):
            result = self.app.decide(item, {"label": question})
            decision = result["label"]
            sources[result.source] += 1
            cost += result.cost_usd
            labelled.append(
                {
                    "i": index,
                    "label": None if decision.uncertain else decision.value,
                    "candidate": decision.value,
                    "confidence": round(decision.confidence, 2),
                    "source": result.source,
                }
            )
            if decision.band is not Band.HIGH or decision.source != "jev":
                review.append(index)
        if review and self.app.writer is not None:
            chosen = self._session_labels(items, review[:SESSION_MAX_ITEMS], instructions, options)
            for index, label in chosen.items():
                entry = labelled[index]
                sources[entry["source"]] -= 1
                sources["session"] += 1
                entry.update(label=label, candidate=label, confidence=None, source="session")
            review = [i for i in review if i not in chosen]
            sources = +sources
        counts = Counter(str(e["label"]) for e in labelled if e["label"] is not None)
        uncertain = [
            {**labelled[i], "text": _clip(items[i], 160)} for i in review if labelled[i]["label"] is None
        ]
        show_all = args.get("return_items", len(items) <= 50)
        result_out: dict[str, Any] = {
            "total": len(items),
            "counts": dict(counts.most_common()),
            "uncertain": uncertain[:20],
            "uncertain_total": len(uncertain),
            "needs_agent_review": bool(review),
            "review_total": len(review),
            "counts_verification": "heuristic_or_unverified" if review else "backend_or_session_llm",
            "items": [{k: v for k, v in e.items() if k != "source"} for e in labelled] if show_all else None,
            "sources": dict(sources),
            "cost_usd": round(cost, 8),
            "llm": self._llm_mode(),
        }
        if uncertain and self.app.writer is None:
            result_out["next_action"] = (
                "Host sem MCP sampling: classifique você os itens em `uncertain` "
                "(o candidate é só um palpite)."
            )
        return result_out

    def rank(self, args: Mapping[str, Any]) -> dict[str, Any]:
        items = _items(args)
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolError("Informe `query`.")
        top_k = int(args.get("top_k") or 5)
        ranked = self.app.rank(query, items)
        source: str | None = ranked[0].decision.source if ranked else None
        needs_review = any(
            hit.decision.source != "jev" or hit.decision.band is not Band.HIGH for hit in ranked
        )
        hits = [(hit.index, round(hit.score, 2)) for hit in ranked]
        if needs_review and self.app.writer is not None and len(ranked) > 1:
            pool = [hit.index for hit in ranked[: max(top_k * 3, 10)]]
            order = self._session_order(query, items, pool, top_k)
            if order:
                scores = dict(hits)
                rest = [(i, scores[i]) for i in pool if i not in order]
                hits = [(i, scores[i]) for i in order] + rest
                source, needs_review = "session", False
        return {
            "total": len(items),
            "top": [{"i": i, "score": score, "text": _clip(items[i], 300)} for i, score in hits[:top_k]],
            "source": source,
            "needs_agent_review": needs_review,
            "llm": self._llm_mode(),
        }

    def summarize_file(self, args: Mapping[str, Any]) -> dict[str, Any]:
        effort = normalize_effort(args.get("effort"))
        source = read_source(str(args.get("path") or ""), int(args.get("max_chars", 60_000)))
        text = source.text
        if not text.strip():
            raise ToolError("O arquivo está vazio: nada para resumir.")
        focus = args.get("instructions") or None
        key = hashlib.sha256(
            json.dumps([text, source.fingerprint, focus, effort], ensure_ascii=False).encode()
        ).hexdigest()
        use_cache = args.get("cache", True)
        if not isinstance(use_cache, bool):
            raise ToolError("cache deve ser true ou false.")
        saved = args.get("save_summary")
        if saved is not None:
            if not isinstance(saved, str) or not saved.strip():
                raise ToolError("save_summary deve ser o texto do resumo.")
            self._remember(key, saved.strip())
            return {"saved": True, "source": source.metadata(), "summary_cache": self._cache_info()}
        summary = self._summaries.get(key) if use_cache else None
        cached = summary is not None
        if cached:
            self._summaries.move_to_end(key)
        elif self.app.writer is None:
            return self._summary_blocks(text, source.metadata(), focus, effort, int(args.get("block") or 1))
        else:
            summary = self.app.summarize(text, focus=focus, effort=effort)
            # Resumos com partes que falharam não devem bloquear uma nova tentativa.
            if use_cache and "ficaram fora deste resumo" not in summary:
                self._remember(key, summary)
        return {
            "summary": summary,
            "source": source.metadata(),
            "cached": cached,
            "verification": "unverified",
            "needs_agent_review": True,
            "llm": self._llm_mode(),
            "next_action": "Confira afirmações com search_file/read_evidence no arquivo original.",
        }

    def diagnose_log(self, args: Mapping[str, Any]) -> dict[str, Any]:
        return diagnose_log(
            str(args.get("path") or ""),
            exit_code=args.get("exit_code"),
            max_groups=int(args.get("max_groups", 12)),
            max_chars=int(args.get("max_chars", MAX_FILE_CHARS)),
        )

    def search_file(self, args: Mapping[str, Any]) -> dict[str, Any]:
        return search_file(
            str(args.get("path") or ""),
            str(args.get("query") or ""),
            max_results=int(args.get("max_results", 8)),
            max_chars=int(args.get("max_chars", MAX_FILE_CHARS)),
        )

    def read_evidence(self, args: Mapping[str, Any]) -> dict[str, Any]:
        return read_evidence(
            str(args.get("path") or ""),
            start_line=int(args.get("start_line", 1)),
            line_count=int(args.get("line_count", 40)),
            fingerprint=args.get("fingerprint"),
            max_chars=int(args.get("max_chars", MAX_FILE_CHARS)),
        )

    def write(self, args: Mapping[str, Any]) -> str | dict[str, Any]:
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise ToolError("Informe `prompt`.")
        effort = normalize_effort(args.get("effort"))
        max_tokens = int(args.get("max_tokens") or WRITE_TOKENS[effort])
        if self.app.writer is None:
            task: dict[str, Any] = {"prompt": prompt, "max_tokens": max_tokens}
            if args.get("system"):
                task["system"] = str(args["system"])
            return {
                "mode": "agent",
                "llm": self._llm_mode(),
                "task": task,
                "context_included": args.get("context") is not None,
                "next_action": (
                    "Host sem MCP sampling: escreva você o texto, seguindo `task` e o contexto que você "
                    "passou. Nenhum outro modelo foi chamado."
                ),
            }
        return self.app.write(
            prompt, context=args.get("context"), system=args.get("system"), max_tokens=max_tokens
        )

    def status(self, args: Mapping[str, Any]) -> dict[str, Any]:
        status = self.app.status()
        writer = self.app.writer
        status["llm"] = {
            "mode": self._llm_mode(),
            "host": self._client or None,
            "sampling": self.sampling,
            "writer": writer.name if writer else None,
            "model": getattr(writer, "model", None) if writer else None,
            "note": (
                "Texto, resumos e incertos vão ao LLM da sessão por MCP sampling."
                if self.sampling
                else (
                    "Writer passado pela biblioteca."
                    if writer is not None
                    else "Host sem MCP sampling: as ferramentas devolvem o trabalho ao agente (mode=agent)."
                )
            ),
        }
        status.pop("writer", None)
        status["summary_cache"] = self._cache_info()
        status["recent_activity"] = list(self._activity)
        return status

    # -- LLM da sessão --------------------------------------------------------------

    def _llm_mode(self) -> str:
        if self.sampling and isinstance(self.app.writer, SamplingWriter):
            return "session_sampling"
        return "writer" if self.app.writer is not None else "agent"

    def _cache_info(self) -> dict[str, Any]:
        return {"entries": len(self._summaries), "scope": "server_session", "limit": SUMMARY_CACHE_LIMIT}

    def _remember(self, key: str, summary: str) -> None:
        self._summaries[key] = summary
        self._summaries.move_to_end(key)
        while len(self._summaries) > SUMMARY_CACHE_LIMIT:
            self._summaries.popitem(last=False)

    def _summary_blocks(
        self, text: str, source: dict[str, Any], focus: str | None, effort: str, start: int
    ) -> dict[str, Any]:
        """Modo agente: os blocos do `effort`, paginados, com o roteiro para o agente resumir."""
        plan = effort_plan(effort)
        blocks = summary_blocks(text, effort=effort)
        if start > len(blocks):
            raise ToolError(f"block deve estar entre 1 e {len(blocks)}.")
        page: list[dict[str, Any]] = []
        used = 0
        for n in range(start, len(blocks) + 1):
            block = blocks[n - 1]
            if page and used + len(block) > AGENT_PAGE_CHARS:
                break
            page.append({"n": n, "text": block})
            used += len(block)
        last = page[-1]["n"]
        focus = focus or DEFAULT_FOCUS
        steps = [
            f"Para cada bloco, extraia o que importa para: {focus} "
            f"(até ~{plan.part_tokens} tokens por bloco).",
            "Os blocos se sobrepõem um pouco: não conte duas vezes o que se repete na emenda.",
            f"Junte os parciais num resumo final (até ~{plan.final_tokens} tokens), sem inventar nada.",
        ]
        if plan.verify:
            steps.append("Revise o resumo final contra os parciais e tire o que não estiver apoiado neles.")
        steps.append(
            "Opcional: grave o resultado com summarize_file(path, instructions, effort, save_summary=...) "
            "para reutilizá-lo nesta sessão."
        )
        return {
            "mode": "agent",
            "llm": self._llm_mode(),
            "summary": None,
            "source": source,
            "effort": effort,
            "plan": {
                "chunk_chars": plan.chunk_chars,
                "overlap_chars": plan.overlap_chars,
                "verify": plan.verify,
            },
            "blocks_total": len(blocks),
            "blocks": page,
            "next_block": last + 1 if last < len(blocks) else None,
            "instructions": steps,
            "verification": "unverified",
            "needs_agent_review": True,
            "next_action": (
                f"Host sem MCP sampling: resuma você. Peça os próximos blocos com block={last + 1}."
                if last < len(blocks)
                else "Host sem MCP sampling: resuma você os blocos acima seguindo `instructions`."
            ),
        }

    def _ask_session(self, prompt: str, max_tokens: int) -> dict[str, Any] | None:
        """Pede ao LLM da sessão um objeto JSON; `None` se não vier um objeto legível."""
        try:
            text = self.app.write(prompt, system=SESSION_JSON_SYSTEM, max_tokens=max_tokens, temperature=0)
        except JevClaudError:
            return None
        return _json_object(text)

    def _session_decide(
        self, state: Any, specs: Mapping[str, Any], compact: dict[str, dict[str, Any]]
    ) -> list[str]:
        """Responde no LLM da sessão as perguntas que a cascata deixou incertas."""
        questions: dict[str, Any] = {}
        for name, spec in specs.items():
            kind = spec.get("type")
            if kind == "choice":
                questions[name] = {
                    "pergunta": spec["instructions"],
                    "opções": list(spec.get("options") or {}),
                }
            elif kind == "noul":
                questions[name] = {"pergunta": spec["instructions"], "opções": ["sim", "não"]}
            elif kind == "score":
                questions[name] = {"pergunta": spec["instructions"], "níveis": list(spec.get("levels") or [])}
        if not questions:
            return []
        prompt = (
            "Responda cada pergunta sobre o ESTADO. Devolva só um objeto JSON nome -> resposta, com "
            "a resposta copiada exatamente de `opções` ou `níveis`.\n\n"
            f"PERGUNTAS:\n{json.dumps(questions, ensure_ascii=False)}\n\nESTADO:\n{_clip_block(state)}"
        )
        answers = self._ask_session(prompt, 60 + 40 * len(questions)) or {}
        resolved: list[str] = []
        for name, question in questions.items():
            answer = answers.get(name)
            out = compact[name]
            if "opções" in question and answer in question["opções"]:
                value: Any = answer if specs[name]["type"] == "choice" else answer == "sim"
                level = None
            elif "níveis" in question and answer in question["níveis"]:
                level = question["níveis"].index(answer)
                value = level
            else:
                continue
            out.update(value=value, abstained=False, source="session", confidence_basis="session_llm")
            out.pop("candidate", None)
            if level is not None:
                out["level"] = level
            resolved.append(name)
        return resolved

    def _session_labels(
        self, items: list[str], indexes: list[int], instructions: str, options: Mapping[str, Any]
    ) -> dict[int, str]:
        labels: dict[int, str] = {}
        for start in range(0, len(indexes), SESSION_BATCH):
            batch = indexes[start : start + SESSION_BATCH]
            listing = "\n".join(f"{i}: {_clip(items[i], 400)}" for i in batch)
            prompt = (
                f"Tarefa: {instructions}\nRótulos (rótulo: descrição):\n"
                + "\n".join(f"- {label}: {desc or ''}" for label, desc in options.items())
                + "\n\nDevolva só um objeto JSON índice -> rótulo, com o rótulo copiado exatamente da "
                f"lista.\n\nITENS:\n{listing}"
            )
            answers = self._ask_session(prompt, 20 + 15 * len(batch)) or {}
            for key, label in answers.items():
                index = int(key) if str(key).isdigit() else -1
                if index in batch and isinstance(label, str) and label in options:
                    labels[index] = label
        return labels

    def _session_order(self, query: str, items: list[str], pool: list[int], top_k: int) -> list[int]:
        listing = "\n".join(f"{i}: {_clip(items[i], 400)}" for i in pool)
        prompt = (
            f"Consulta: {query}\n\nOrdene os itens pela relevância para a consulta e devolva só um objeto "
            f'JSON {{"top": [índices]}} com os {min(top_k, len(pool))} mais relevantes, do mais para o '
            f"menos relevante.\n\nITENS:\n{listing}"
        )
        answer = self._ask_session(prompt, 20 + 8 * len(pool)) or {}
        order: list[int] = []
        for value in answer.get("top") or []:
            index = int(value) if str(value).isdigit() else -1
            if index in pool and index not in order:
                order.append(index)
        return order


SUMMARY_CACHE_LIMIT = 16
SESSION_JSON_SYSTEM = (
    "Você responde só com um objeto JSON válido, sem texto antes ou depois e sem bloco de código."
)


def _json_object(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _clip_block(state: Any, limit: int = 20_000) -> str:
    text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "\n[...cortado]"


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
        "value": None
        if decision.uncertain
        else (decision.value.as_dict() if isinstance(decision.value, Region) else decision.value),
        "confidence": round(decision.confidence, 3),
        "band": decision.band.value,
        "abstained": decision.uncertain,
        "source": decision.source,
        "confidence_basis": "lexical_heuristic" if decision.source == "offline" else "backend_or_cache",
    }
    if decision.uncertain:
        out["candidate"] = decision.value.as_dict() if isinstance(decision.value, Region) else decision.value
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
    source = read_source(path, max_chars)
    if source.truncated:
        raise ToolError(
            f"Arquivo excede {max_chars} caracteres; divida em partes. Nenhum item foi processado."
        )
    return source.text


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _text(text: str, *, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def serve(stdin: BinaryIO | None = None, stdout: BinaryIO | None = None, server: Server | None = None) -> int:
    """Loop stdio: lê uma mensagem JSON por linha e responde na mesma ordem.

    O mesmo canal leva os pedidos do servidor ao host (MCP sampling) durante uma ferramenta.
    """
    channel = Channel(stdin or sys.stdin.buffer, stdout or sys.stdout.buffer)
    if server is None:
        server = Server(channel=channel)
    elif server._channel is None:
        server._channel = channel
    try:
        for message in channel.messages():
            if message is _INVALID:
                response: dict[str, Any] | None = _error(None, -32700, "JSON inválido")
            elif isinstance(message, dict) and "method" not in message and "id" in message:
                continue  # resposta atrasada a um pedido que já desistimos de esperar
            else:
                response = server.handle(message)
            if response is not None:
                channel.send(response)
    finally:
        server.close()
    return 0
