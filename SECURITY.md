# Segurança

## Reportando vulnerabilidades

Não abra uma issue pública. Use o recurso **Security > Report a vulnerability** do GitHub
(aviso privado) com uma descrição e, se possível, um exemplo mínimo. A resposta inicial sai
em até 7 dias.

## Boas práticas ao usar o jev-claude

- **Chaves ficam no servidor.** `TYPESAFE_API_KEY` e `JEV_CLAUDE_LLM_API_KEY` nunca devem ir
  para o navegador ou para o repositório. Use variáveis de ambiente ou um cofre de segredos.
- **O cache guarda estado e respostas** em SQLite local (`JEV_CLAUDE_HOME`). Se o estado tiver
  dados pessoais, proteja o diretório ou desligue o cache (`JEV_CLAUDE_CACHE_TTL_DAYS=0`).
- **Saída tipada garante a interface, não a verdade.** Decisões com impacto (pagamentos,
  exclusões, acesso) devem exigir confiança alta (`Policy`) e, quando possível, revisão humana.
- O SDK do TypeSafe registra corpos de requisição no log em nível debug; não ative
  `TYPESAFE_LOG_LEVEL=debug` em produção com dados sensíveis.
