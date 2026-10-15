"""ramifica — decisões tipadas para o código (TypeSafe Jev) e texto para pessoas (LLM).

>>> from ramifica import Ramifica, choice, noul
>>> r = Ramifica()
>>> d = r.decide("Fui cobrado duas vezes!", {
...     "setor": choice("Qual setor?", {"financeiro": "cobrança, fatura", "suporte": "erro, bug"}),
...     "urgente": noul("A mensagem pede ação imediata?"),
... })
>>> d["setor"].value
'financeiro'
"""

from ramifica.backends import JevBackend, OfflineBackend
from ramifica.budget import Budget
from ramifica.config import Settings
from ramifica.core import Case, Ramifica, Ranked
from ramifica.decision import Band, Decision, Decisions, Policy
from ramifica.errors import (
    BackendError,
    BudgetExceeded,
    NoWriterError,
    QuestionError,
    RamificaError,
    WriterError,
)
from ramifica.questions import Choice, Noul, Score, choice, noul, score
from ramifica.store import Store
from ramifica.writers import CallableWriter, OllamaWriter, OpenAICompatibleWriter, Writer

__version__ = "0.1.0"

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
    "NoWriterError",
    "Noul",
    "OfflineBackend",
    "OllamaWriter",
    "OpenAICompatibleWriter",
    "Policy",
    "QuestionError",
    "Ramifica",
    "RamificaError",
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
