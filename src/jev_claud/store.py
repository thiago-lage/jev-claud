"""Armazenamento local em SQLite: cache de decisões, livro-caixa de gastos e metadados.

O cache é o que deixa decisões repetidas com custo zero: a mesma pergunta sobre o mesmo
estado, no mesmo modelo, não volta a ser cobrada enquanto não expirar.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jev_claud.envvars import getenv

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created REAL NOT NULL,
    expires REAL
);
CREATE TABLE IF NOT EXISTS ledger (
    month TEXT PRIMARY KEY,
    usd REAL NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    calls INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS routing (
    month TEXT NOT NULL,
    layer TEXT NOT NULL,
    decisions INTEGER NOT NULL DEFAULT 0,
    saved_tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (month, layer)
);
CREATE TABLE IF NOT EXISTS savings (
    month TEXT NOT NULL,
    kind TEXT NOT NULL,
    events INTEGER NOT NULL DEFAULT 0,
    tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (month, kind)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated REAL NOT NULL
);
"""


def default_home() -> Path:
    """Diretório de dados: `JEV_CLAUD_HOME`, ou o cache do usuário no sistema operacional.

    Na primeira execução depois da troca de nome, a pasta antiga (`jev-claude`, com a chave
    do Jev, o cache e os gastos) é renomeada para `jev-claud`; se não der, ela é usada como está.
    """
    configured = getenv("JEV_CLAUD_HOME").strip()
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"])
    else:
        xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
        base = Path(xdg) if xdg else Path.home() / ".cache"
    home, old = base / "jev-claud", base / "jev-claude"
    if not home.exists() and old.is_dir():
        try:
            old.rename(home)
        except OSError:
            return old
    return home


def _default_db(home: Path) -> Path:
    """`jev_claud.sqlite3`; o banco da 0.6 (`jev_claude.sqlite3`) é renomeado na primeira vez."""
    db, old = home / "jev_claud.sqlite3", home / "jev_claude.sqlite3"
    if not db.exists() and old.exists():
        for suffix in ("", "-wal", "-shm"):  # arquivos do modo WAL vão junto
            source = Path(f"{old}{suffix}")
            if source.exists():
                try:
                    source.rename(f"{db}{suffix}")
                except OSError:
                    return old
    return db


def current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


class Store:
    """Banco SQLite seguro para uso entre threads. Use `":memory:"` para não tocar o disco."""

    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            path = _default_db(default_home())
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        if self.path != ":memory:":
            with contextlib.suppress(sqlite3.DatabaseError):  # WAL é otimização, não requisito
                self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    # -- cache ---------------------------------------------------------------

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT value, expires FROM cache WHERE key = ?", (key,)).fetchone()
            if row is None:
                return None
            value, expires = row
            if expires is not None and expires < time.time():
                self._conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                return None
        loaded: dict[str, Any] = json.loads(value)
        return loaded

    def put(self, key: str, value: dict[str, Any], ttl: float | None = None) -> None:
        now = time.time()
        expires = now + ttl if ttl is not None else None
        payload = json.dumps(value, ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, created, expires) VALUES (?, ?, ?, ?)",
                (key, payload, now, expires),
            )

    def cache_size(self) -> int:
        with self._lock:
            (count,) = self._conn.execute("SELECT COUNT(*) FROM cache").fetchone()
        return int(count)

    def clear_cache(self) -> int:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM cache")
        return cursor.rowcount

    # -- livro-caixa ---------------------------------------------------------

    def record(self, usd: float, input_tokens: int, month: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO ledger (month, usd, input_tokens, calls) VALUES (?, ?, ?, 1)
                ON CONFLICT(month) DO UPDATE SET
                    usd = usd + excluded.usd,
                    input_tokens = input_tokens + excluded.input_tokens,
                    calls = calls + 1
                """,
                (month or current_month(), usd, input_tokens),
            )

    def spent(self, month: str | None = None) -> tuple[float, int, int]:
        """(dólares, tokens de entrada, chamadas) gastos no mês."""
        with self._lock:
            row = self._conn.execute(
                "SELECT usd, input_tokens, calls FROM ledger WHERE month = ?", (month or current_month(),)
            ).fetchone()
        return (float(row[0]), int(row[1]), int(row[2])) if row else (0.0, 0, 0)

    def ledger(self) -> list[tuple[str, float, int, int]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT month, usd, input_tokens, calls FROM ledger ORDER BY month"
            ).fetchall()
        return [(str(m), float(u), int(t), int(c)) for m, u, t, c in rows]

    # -- roteamento (quem respondeu cada pergunta) -----------------------------

    def record_routing(
        self, layer: str, decisions: int, saved_tokens: int = 0, month: str | None = None
    ) -> None:
        if decisions <= 0:
            return
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO routing (month, layer, decisions, saved_tokens) VALUES (?, ?, ?, ?)
                ON CONFLICT(month, layer) DO UPDATE SET
                    decisions = decisions + excluded.decisions,
                    saved_tokens = saved_tokens + excluded.saved_tokens
                """,
                (month or current_month(), layer, decisions, max(0, saved_tokens)),
            )

    def routing(self, month: str | None = None) -> dict[str, tuple[int, int]]:
        """Camada -> (perguntas respondidas, tokens do Jev economizados) no mês."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT layer, decisions, saved_tokens FROM routing WHERE month = ?",
                (month or current_month(),),
            ).fetchall()
        return {str(layer): (int(n), int(saved)) for layer, n, saved in rows}

    # -- economia de contexto do Claude (compactação, ferramentas MCP) --------

    def record_saving(self, kind: str, tokens: int, month: str | None = None) -> None:
        if tokens <= 0:
            return
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO savings (month, kind, events, tokens) VALUES (?, ?, 1, ?)
                ON CONFLICT(month, kind) DO UPDATE SET
                    events = events + 1,
                    tokens = tokens + excluded.tokens
                """,
                (month or current_month(), kind, int(tokens)),
            )

    def savings(self, month: str | None = None) -> dict[str, tuple[int, int]]:
        """Tipo -> (vezes, tokens do Claude economizados) no mês."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind, events, tokens FROM savings WHERE month = ?", (month or current_month(),)
            ).fetchall()
        return {str(kind): (int(n), int(tokens)) for kind, n, tokens in rows}

    # -- metadados -----------------------------------------------------------

    def meta_get(self, key: str, max_age: float | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value, updated FROM meta WHERE key = ?", (key,)).fetchone()
        if row is None or (max_age is not None and time.time() - row[1] > max_age):
            return None
        return str(row[0])

    def meta_put(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value, updated) VALUES (?, ?, ?)",
                (key, value, time.time()),
            )

    def meta_delete(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM meta WHERE key = ?", (key,))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
