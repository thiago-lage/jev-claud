# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/); versões seguem
[SemVer](https://semver.org/lang/pt-BR/).

## [0.3.1] - 2026-10-15

### Corrigido

- Windows: `jev-claude update` podia deixar a instalação quebrada ao tentar substituir o
  `jev-claude.exe` em execução. O atalho do instalador agora chama `python -m jev_claude`, e o
  `update` recusa a autoatualização quando roda pelo `.exe`, explicando o comando certo.

## [0.3.0] - 2026-10-15

### Adicionado

- Servidor MCP (`jev-claude mcp`, sem dependências): o Claude Code delega trabalho em lote ao
  Jev e ao Ollama local com as ferramentas `decide`, `classify`, `rank`, `summarize_file`,
  `write` e `status`. Os dados volumosos são lidos localmente e não entram no contexto do Claude.
- `jev-claude setup` registra o servidor MCP no Claude Code (`claude mcp add -s user`).
- `python -m jev_claude`.

## [0.2.0] - 2026-10-15

### Adicionado

- `jev-claude setup`: assistente no terminal, no estilo do Claude Code, que detecta e instala
  (com confirmação) Ollama + servidor + modelo, SDK do Jev, Claude Code, skill TypeSafe e a regra
  global no `~/.claude/CLAUDE.md`. Modos `--yes` e `--check`. Abre sozinho no primeiro `jev-claude`.
- Instaladores de uma linha: `install.ps1` (Windows) e `install.sh` (macOS/Linux), com ambiente
  isolado em `~/.local/share/jev-claude` e comando em `~/.local/bin`.
- `config.json` (chave do Jev, orçamento, modelos), com variáveis de ambiente tendo prioridade.
- `jev-claude update` também atualiza o próprio jev-claude.
- CI testa os instaladores em Ubuntu, macOS e Windows.

### Segurança

- O SDK do Jev nunca é instalado automaticamente no Python global (risco de conflito de versões).

## [0.1.0] - 2026-10-15

### Adicionado

- `JevClaude.decide/ask/when/switch/rank` para decisões tipadas (Choice, Noul, Score) via TypeSafe Jev.
- `JevClaude.write` para texto livre via LLM (Ollama, endpoint compatível com OpenAI, ou função).
- Backend offline gratuito (heurística lexical) para custo zero, testes e fallback.
- Cache SQLite de decisões, livro-caixa mensal e orçamento com teto (`JEV_CLAUDE_BUDGET_USD`, padrão 0).
- Resolução automática de modelo: `latest`, `preview`, `newest` ou versão fixa.
- CLI: `doctor`, `ask`, `write`, `models`, `spend`, `update`, `cache`.
- API async (`adecide`, `aask`, `awrite`).
