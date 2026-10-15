"""Backends de decisão: `JevBackend` (TypeSafe, pago) e `OfflineBackend` (local, grátis)."""

from ramifica.backends.base import Backend, RawAnswer, RawResult
from ramifica.backends.jev import JevBackend, ModelInfo
from ramifica.backends.offline import OfflineBackend

__all__ = ["Backend", "JevBackend", "ModelInfo", "OfflineBackend", "RawAnswer", "RawResult"]
