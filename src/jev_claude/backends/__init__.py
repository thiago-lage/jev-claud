"""Backends de decisão: `JevBackend` (TypeSafe, pago) e `OfflineBackend` (local, grátis)."""

from jev_claude.backends.base import Backend, RawAnswer, RawResult
from jev_claude.backends.jev import JevBackend, ModelInfo
from jev_claude.backends.offline import OfflineBackend

__all__ = ["Backend", "JevBackend", "ModelInfo", "OfflineBackend", "RawAnswer", "RawResult"]
