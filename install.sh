#!/usr/bin/env bash
# jev-claude - instalador para macOS e Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/thiago-lage/jev-claude/main/install.sh | bash
#
# 1. Encontra o Python 3.10+.
# 2. Cria um ambiente isolado em ~/.local/share/jev-claude (nao mexe no Python do sistema).
# 3. Coloca o comando `jev-claude` em ~/.local/bin e garante que essa pasta esta no PATH.
# 4. Abre o assistente `jev-claude setup` (Ollama, Claude Code, skill TypeSafe, chave do Jev).
#
# Variaveis opcionais: JEV_CLAUDE_SPEC (o que o pip instala), JEV_CLAUDE_INSTALL_DIR,
# JEV_CLAUDE_BIN_DIR, JEV_CLAUDE_NO_MODIFY_PATH=1, JEV_CLAUDE_SKIP_SETUP=1.
set -euo pipefail

SPEC="${JEV_CLAUDE_SPEC:-jev-claude[jev] @ git+https://github.com/thiago-lage/jev-claude}"
INSTALL_DIR="${JEV_CLAUDE_INSTALL_DIR:-$HOME/.local/share/jev-claude}"
BIN_DIR="${JEV_CLAUDE_BIN_DIR:-$HOME/.local/bin}"

accent=$'\033[38;5;173m'; green=$'\033[32m'; dim=$'\033[2m'; reset=$'\033[0m'
if [ ! -t 1 ] || [ -n "${NO_COLOR:-}" ]; then accent=""; green=""; dim=""; reset=""; fi
step() { printf '  %s*%s %s\n' "$accent" "$reset" "$1"; }
ok() { printf '  %s+%s %s\n' "$green" "$reset" "$1"; }
fail() { printf '  x %s\n' "$1" >&2; exit 1; }

printf '\n  %sjev-claude - instalador%s\n' "$accent" "$reset"
printf '  %sJev decide o que o codigo ramifica. LLM escreve o que a pessoa le.%s\n\n' "$dim" "$reset"

# 1. Python ---------------------------------------------------------------------------
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  case "$(uname -s)" in
    Darwin) fail "Python 3.10+ nao encontrado. Instale com: brew install python@3.12" ;;
    *) fail "Python 3.10+ nao encontrado. Instale com o gerenciador da sua distro (ex.: sudo apt install python3 python3-venv)" ;;
  esac
fi
ok "Python: $("$PYTHON" -c 'import sys; print(sys.version.split()[0])')"

# 2. Ambiente isolado -------------------------------------------------------------------
VENV="$INSTALL_DIR/venv"
if [ ! -x "$VENV/bin/python" ]; then
  step "Criando ambiente isolado em $VENV"
  mkdir -p "$INSTALL_DIR"
  "$PYTHON" -m venv "$VENV" || fail "Falha ao criar o ambiente (no Debian/Ubuntu: sudo apt install python3-venv)"
fi
step "Instalando jev-claude"
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade "$SPEC" || fail "Falha ao instalar: $SPEC"
ok "Instalado: $("$VENV/bin/jev-claude" --version)"

# 3. Comando no PATH ---------------------------------------------------------------------
mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/jev-claude" "$BIN_DIR/jev-claude"
ok "Comando: $BIN_DIR/jev-claude"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    if [ -z "${JEV_CLAUDE_NO_MODIFY_PATH:-}" ]; then
      case "${SHELL:-}" in
        */zsh) RC="$HOME/.zshrc" ;;
        */bash) RC="$HOME/.bashrc" ;;
        *) RC="$HOME/.profile" ;;
      esac
      LINE="export PATH=\"$BIN_DIR:\$PATH\""
      if ! grep -qsF "$LINE" "$RC"; then
        printf '\n# jev-claude\n%s\n' "$LINE" >> "$RC"
        ok "$BIN_DIR adicionado ao PATH em $RC"
      fi
    fi
    export PATH="$BIN_DIR:$PATH"
    ;;
esac

# 4. Assistente --------------------------------------------------------------------------
echo
if [ -n "${JEV_CLAUDE_SKIP_SETUP:-}" ]; then
  ok "Pronto. Rode: jev-claude setup"
elif [ -r /dev/tty ] && (: < /dev/tty) 2>/dev/null; then
  # Com `curl | bash` o stdin e o script; as perguntas leem do terminal.
  "$VENV/bin/jev-claude" setup < /dev/tty
  printf '\n  %sAbra um terminal novo para usar o comando jev-claude em qualquer pasta.%s\n' "$dim" "$reset"
else
  "$VENV/bin/jev-claude" setup --check || true
  ok "Sem terminal interativo. Rode depois: jev-claude setup"
fi
