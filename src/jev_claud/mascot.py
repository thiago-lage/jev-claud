"""Jevin, o mascote do jev-claud: um robozinho em pixel art com a antena em forquilha.

O corpo segue as proporções do bonequinho do Claude Code (olhos, bracinhos e quatro
perninhas); a forquilha é a bifurcação do código, o lado para onde o Jev decide ramificar.
Desenhado com blocos de quadrante (quatro pixels por caractere); sem suporte a esses
caracteres, cai para uma versão em ASCII.
"""

from __future__ import annotations

NAME = "Jevin"

PIXELS = (
    "      X    X      ",
    "       X  X       ",
    "   XXXXXXXXXXXX   ",
    "   XX XXXXXX XX   ",
    " XXXXXXXXXXXXXXXX ",
    "   XXXXXXXXXXXX   ",
    "    X X    X X    ",
)

ASCII = (
    r"   \ /   ",
    r"  [o_o]  ",
    r" -|___|- ",
    r"   ' '   ",
)

# (cima-esquerda, cima-direita, baixo-esquerda, baixo-direita) -> caractere.
_QUADRANTS = {
    (0, 0, 0, 0): " ",
    (1, 0, 0, 0): "▘",
    (0, 1, 0, 0): "▝",
    (0, 0, 1, 0): "▖",
    (0, 0, 0, 1): "▗",
    (1, 1, 0, 0): "▀",
    (0, 0, 1, 1): "▄",
    (1, 0, 1, 0): "▌",
    (0, 1, 0, 1): "▐",
    (1, 0, 0, 1): "▚",
    (0, 1, 1, 0): "▞",
    (1, 1, 1, 0): "▛",
    (1, 1, 0, 1): "▜",
    (1, 0, 1, 1): "▙",
    (0, 1, 1, 1): "▟",
    (1, 1, 1, 1): "█",
}

BLOCKS = "".join(sorted(set(_QUADRANTS.values()) - {" "}))


def render(fancy: bool = True) -> list[str]:
    """Linhas do mascote, todas com a mesma largura."""
    if not fancy:
        return list(ASCII)
    width = max(map(len, PIXELS))
    width += width % 2
    rows = [row.ljust(width) for row in PIXELS]
    if len(rows) % 2:
        rows.append(" " * width)
    lines = []
    for y in range(0, len(rows), 2):
        line = ""
        top, bottom = rows[y], rows[y + 1]
        for x in range(0, width, 2):
            cell = (top[x] != " ", top[x + 1] != " ", bottom[x] != " ", bottom[x + 1] != " ")
            line += _QUADRANTS[(int(cell[0]), int(cell[1]), int(cell[2]), int(cell[3]))]
        lines.append(line)
    return lines
