"""jev-claud — decisões tipadas para o código (TypeSafe Jev) e texto para pessoas (LLM).

>>> from jev_claud import JevClaud, choice, noul
>>> r = JevClaud()
>>> d = r.decide("Fui cobrado duas vezes!", {
...     "setor": choice("Qual setor?", {"financeiro": "cobrança, fatura", "suporte": "erro, bug"}),
...     "urgente": noul("A mensagem pede ação imediata?"),
... })
>>> d["setor"].value
'financeiro'
"""

from jev_claud.backends import JevBackend, OfflineBackend
from jev_claud.budget import Budget
from jev_claud.config import Settings
from jev_claud.core import Case, JevClaud, Ranked
from jev_claud.decision import Band, Decision, Decisions, Policy, Region
from jev_claud.errors import (
    BackendError,
    BudgetExceeded,
    JevClaudError,
    NoWriterError,
    QuestionError,
    WriterError,
)
from jev_claud.media import Image
from jev_claud.questions import Box, Choice, Noul, Score, box, choice, noul, score
from jev_claud.store import Store
from jev_claud.writers import CallableWriter, OllamaWriter, OpenAICompatibleWriter, Writer

# Nomes antigos (até a 0.6), para código existente não quebrar.
JevClaude = JevClaud
JevClaudeError = JevClaudError

__version__ = "0.8.2"

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
    "JevClaud",
    "JevClaudError",
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
