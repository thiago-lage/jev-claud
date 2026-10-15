#!/bin/sh
# Hook PreToolUse (Bash) do plugin Jev.
# Repassa o evento ao `jev-claude hook pre-bash`, que pode reescrever um teste/build simples
# para `python -m jev_claude run -- <comando>` (saída compactada, mesmo código de saída).
# Sem o jev-claude instalado (`pip install jev-claude`), não faz nada.
command -v jev-claude >/dev/null 2>&1 || exit 0
exec jev-claude hook pre-bash
