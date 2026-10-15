#!/bin/sh
# Hook PreToolUse (Bash) do plugin Jevin.
# Repassa o evento ao `jev-claud hook pre-bash`, que pode reescrever um teste/build simples
# para `python -m jev_claud run -- <comando>` (saída compactada, mesmo código de saída).
# Sem o jev-claud instalado (`pip install jev-claud`), não faz nada. Não acessa a rede.
command -v jev-claud >/dev/null 2>&1 || exit 0
exec jev-claud hook pre-bash
