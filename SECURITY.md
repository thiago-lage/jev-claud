# Segurança

## Reportando vulnerabilidades

Não abra uma issue pública. Use o recurso **Security > Report a vulnerability** do GitHub
(aviso privado) com uma descrição e, se possível, um exemplo mínimo. A resposta inicial sai
em até 7 dias.

## Boas práticas ao usar o jev-claud

- **Chaves ficam no servidor.** `TYPESAFE_API_KEY` e `JEV_CLAUD_LLM_API_KEY` nunca devem ir
  para o navegador ou para o repositório. Use variáveis de ambiente ou um cofre de segredos.
- **O `jev-claud setup` guarda a chave do Jev em `config.json`** (em `JEV_CLAUD_HOME`), com
  permissão só para o seu usuário. Em servidores, prefira a variável `TYPESAFE_API_KEY`.
- **Os instaladores** baixam apenas de fontes oficiais (PyPI, GitHub deste repositório, winget,
  Homebrew, ollama.com, claude.ai) e o assistente pergunta antes de cada instalação.
- **O cache guarda as respostas** em SQLite local (`JEV_CLAUD_HOME`), com um hash do estado
  como chave. As respostas podem conter trechos extraídos do estado: se ele tiver dados pessoais, proteja o diretório ou desligue o cache (`JEV_CLAUD_CACHE_TTL_DAYS=0`).
- **Saída tipada garante a interface, não a verdade.** Decisões com impacto (pagamentos,
  exclusões, acesso) devem exigir confiança alta (`Policy`) e, quando possível, revisão humana.

O que o jev-claud guarda e envia está descrito na [política de privacidade](PRIVACY.md).
- O SDK do TypeSafe registra corpos de requisição no log em nível debug; não ative
  `TYPESAFE_LOG_LEVEL=debug` em produção com dados sensíveis.
