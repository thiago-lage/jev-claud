# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/); versões seguem
[SemVer](https://semver.org/lang/pt-BR/).

## [0.1.0] - 2026-10-15

### Adicionado

- `JevClaude.decide/ask/when/switch/rank` para decisões tipadas (Choice, Noul, Score) via TypeSafe Jev.
- `JevClaude.write` para texto livre via LLM (Ollama, endpoint compatível com OpenAI, ou função).
- Backend offline gratuito (heurística lexical) para custo zero, testes e fallback.
- Cache SQLite de decisões, livro-caixa mensal e orçamento com teto (`JEV_CLAUDE_BUDGET_USD`, padrão 0).
- Resolução automática de modelo: `latest`, `preview`, `newest` ou versão fixa.
- CLI: `doctor`, `ask`, `write`, `models`, `spend`, `update`, `cache`.
- API async (`adecide`, `aask`, `awrite`).
