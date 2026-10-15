# Suporte

Ajuda com o plugin **Jevin** e com o pacote **jev-claud**.

## Antes de abrir uma issue

1. **Atualize:** `jev-claud update` e depois `jev-claud setup`. No Claude Code, atualize o
   plugin pelo `/plugin`, no marketplace `jev-claud`.
2. **Veja o que está ativo:** `jev-claud doctor` mostra a versão, o Jev, o Ollama, o
   orçamento e o cache. `jev-claud setup --check` lista o que falta sem instalar nada.
3. **Consulte a documentação:**
   - [Plugin Jevin](plugin/README.md): instalação, uso da skill e do subagente, problemas comuns.
   - [README do projeto](README.md): biblioteca, servidor MCP, saídas compactadas e configuração.

## Problemas comuns

| Sintoma | O que fazer |
| --- | --- |
| A saída dos testes não chega compactada | Rode `jev-claud setup` (o hook fica no `~/.claude/settings.json` desde a 0.8) e abra uma sessão nova do Claude Code. Confira se `JEV_CLAUD_COMPACT` não está em `0` |
| `jev-claud: command not found` | Abra um terminal novo (o instalador adiciona `~/.local/bin` ao PATH) ou rode o instalador de novo |
| O Claude não usa as ferramentas MCP | Confira `claude mcp list` (deve aparecer `jev-claud`) e abra uma sessão nova |
| O plugin aparece com o nome antigo (`jev` ou `jev-claude`) | Rode `jev-claud setup`: ele troca pelos nomes novos |
| O `jevin@synced` (do diretório do Claude) aparece como "not loaded" | A cópia do marketplace também está ativa. Rode `claude plugin uninstall jevin@jev-claud` (ou `jev-claud setup`) e abra uma sessão nova |
| O Ollama não responde | `ollama serve` e `jev-claud setup` (que baixa o modelo, se faltar) |

## Abrir uma issue

[Abra uma issue](https://github.com/thiago-lage/jev-claud/issues/new/choose) escolhendo o
formulário certo:

- **Problema:** algo não funciona como a documentação diz. Cole a saída do `jev-claud doctor`.
- **Dúvida ou sugestão:** como fazer algo, ou uma ideia de melhoria.

## Segurança e privacidade

- Vulnerabilidades: **não** abra uma issue pública. Siga o [SECURITY.md](SECURITY.md).
- O que o jev-claud guarda e envia: [PRIVACY.md](PRIVACY.md).
