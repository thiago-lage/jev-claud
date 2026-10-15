"""jev-claude — decisões tipadas para o código (TypeSafe Jev) e texto para pessoas (LLM).

>>> from jev_claude import JevClaude, choice, noul
>>> r = JevClaude()
>>> d = r.decide("Fui cobrado duas vezes!", {
...     "setor": choice("Qual setor?", {"financeiro": "cobrança, fatura", "suporte": "erro, bug"}),
...     "urgente": noul("A mensagem pede ação imediata?"),
... })
>>> d["setor"].value
'financeiro'
"""

from jev_claude.backends import JevBackend, OfflineBackend
from jev_claude.budget import Budget
from jev_claude.config import Settings
from jev_claude.core import Case, JevClaude, Ranked
from jev_claude.decision import Band, Decision, Decisions, Policy
from jev_claude.errors import (
    BackendError,
    BudgetExceeded,
    JevClaudeError,
    NoWriterError,
    QuestionError,
    WriterError,
)
from jev_claude.questions import Choice, Noul, Score, choice, noul, score
from jev_claude.store import Store
from jev_claude.writers import CallableWriter, OllamaWriter, OpenAICompatibleWriter, Writer

__version__ = "0.2.0"

__all__ = [
    "BackendError",
    "Band",
    "Budget",
    "BudgetExceeded",
    "CallableWriter",
    "Case",
    "Choice",
    "Decision",
    "Decisions",
    "JevBackend",
    "JevClaude",
    "JevClaudeError",
    "NoWriterError",
    "Noul",
    "OfflineBackend",
    "OllamaWriter",
    "OpenAICompatibleWriter",
    "Policy",
    "QuestionError",
    "Ranked",
    "Score",
    "Settings",
    "Store",
    "Writer",
    "WriterError",
    "__version__",
    "choice",
    "noul",
    "score",
]
