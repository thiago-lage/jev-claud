"""Exceções do jev-claude."""

from __future__ import annotations


class JevClaudeError(Exception):
    """Base de todas as exceções do jev-claude."""


class QuestionError(JevClaudeError, ValueError):
    """Pergunta mal formada (ex.: Choice sem opções, Score com níveis demais)."""


class BudgetExceeded(JevClaudeError):
    """A chamada paga ultrapassaria o orçamento mensal configurado."""

    def __init__(self, estimated_usd: float, spent_usd: float, budget_usd: float) -> None:
        super().__init__(
            f"Orçamento mensal de US$ {budget_usd:.4f} seria ultrapassado "
            f"(gasto: US$ {spent_usd:.6f}, estimativa desta chamada: US$ {estimated_usd:.6f}). "
            "Aumente JEV_CLAUDE_BUDGET_USD para permitir chamadas pagas ao Jev."
        )
        self.estimated_usd = estimated_usd
        self.spent_usd = spent_usd
        self.budget_usd = budget_usd


class BackendError(JevClaudeError):
    """Falha ao consultar um backend de decisão (ex.: Jev fora do ar)."""


class NoWriterError(JevClaudeError):
    """Nenhuma LLM configurada para gerar texto."""


class WriterError(JevClaudeError):
    """A LLM de texto respondeu com erro."""
