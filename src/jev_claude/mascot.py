"""O mascote do jev-claude: um robozinho em pixel art com a antena em forquilha.

A forquilha é a bifurcação do código: o Jev decide para qual lado ele ramifica.
Desenhado com meio-blocos (dois pixels por linha de terminal); sem suporte a esses
caracteres, cai para uma versão em ASCII.
"""

from __future__ import annotations

PIXELS = (
    " X     X ",
    "  X   X  ",
    "   X X   ",
    "    X    ",
    " XXXXXXX ",
    " X XXX X ",
    " XXXXXXX ",
    "  X   X  ",
)

ASCII = (
    r"  \ /  ",
    r" [o_o] ",
    r"  / \  ",
)

BLOCKS = "█▀▄"


def render(fancy: bool = True) -> list[str]:
    """Linhas do mascote, todas com a mesma largura."""
    if not fancy:
        return list(ASCII)
    width = max(map(len, PIXELS))
    rows = [row.ljust(width) for row in PIXELS]
    if len(rows) % 2:
        rows.append(" " * width)
    lines = []
    for top, bottom in zip(rows[::2], rows[1::2], strict=True):
        line = ""
        for a, b in zip(top, bottom, strict=True):
            up, down = a != " ", b != " "
            line += "█" if up and down else "▀" if up else "▄" if down else " "
        lines.append(line)
    return lines
