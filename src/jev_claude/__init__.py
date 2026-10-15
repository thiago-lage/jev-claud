"""Nome antigo do pacote (até a 0.6). Use `jev_claud`: este módulo só repassa para ele.

`from jev_claude import JevClaude` e `python -m jev_claude` continuam funcionando, para o
código e os atalhos instalados antes da troca de nome não quebrarem.
"""

from jev_claud import *  # noqa: F403
from jev_claud import __all__ as _all
from jev_claud import __version__

__all__ = [*_all, "__version__"]
