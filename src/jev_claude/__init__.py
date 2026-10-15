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
from jev_claude.decision import Band, Decision, Decisions, Policy, Region
from jev_claude.errors import (
    BackendError,
    BudgetExceeded,
    JevClaudeError,
    NoWriterError,
    QuestionError,
    WriterError,
)
from jev_claude.media import Image
from jev_claude.questions import Box, Choice, Noul, Score, box, choice, noul, score
from jev_claude.store import Store
from jev_claude.writers import CallableWriter, OllamaWriter, OpenAICompatibleWriter, Writer

__version__ = "0.6.2"

__all__ = [
    "BackendError",
    "Band",
    "Box",
    "Budget",
    "BudgetExceeded",
    "CallableWriter",
    "Case",
    "Choice",
    "Decision",
    "Decisions",
    "Image",
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
    "Region",
    "Score",
    "Settings",
    "Store",
    "Writer",
    "WriterError",
    "__version__",
    "box",
    "choice",
    "noul",
    "score",
]
