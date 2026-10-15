# Política de privacidade

Vale para o plugin **Jevin** (Claude Code e Cowork) e para o pacote Python **jev-claud**
(biblioteca, comando `jev-claud`, servidor MCP e hook de compactação). Última atualização:
15 de outubro de 2026.

## Resumo

- O autor **não recebe nenhum dado seu**: não há servidor do jev-claud, conta, telemetria nem
  coleta de uso.
- O plugin Jevin é só texto (uma skill e um subagente). Ele não roda programas, não grava
  arquivos e não acessa a rede.
- O pacote jev-claud guarda tudo **no seu computador**. Ele só envia dados para serviços que
  **você** configura: a API do TypeSafe Jev (com chave e orçamento) e uma LLM de texto (o Ollama
  local, por padrão).

## Plugin Jevin

O plugin tem dois arquivos em Markdown que o Claude lê quando são relevantes:

- **Skill `jev`:** orientações sobre quando usar decisões tipadas, texto de LLM ou código puro.
- **Subagente `scout`:** explora o código em um contexto separado e devolve só a conclusão. Ele
  usa as ferramentas `Read`, `Grep`, `Glob` e `Bash` do próprio Claude Code, com as mesmas
  permissões e perguntas de sempre, e é instruído a só ler (nada de editar, instalar ou acessar
  a rede).

O que o Claude lê ou escreve durante a sessão segue a política da Anthropic, não esta.

## Pacote jev-claud: o que fica no seu computador

Tudo fica na pasta de dados do jev-claud: `%LOCALAPPDATA%\jev-claud` no Windows,
`~/.cache/jev-claud` no macOS e no Linux (ou `$XDG_CACHE_HOME/jev-claud`), ou o caminho em
`JEV_CLAUD_HOME`.

| O quê | Onde | Para quê |
| --- | --- | --- |
| Chave e orçamento do Jev, modelo do Ollama, versão do setup | `config.json` (no macOS e no Linux, com permissão só para o seu usuário) | Configuração feita pelo `jev-claud setup` |
| Respostas de decisões já tomadas | `jev_claud.sqlite3`, tabela `cache` | Não pagar duas vezes pela mesma pergunta. A chave do cache é um hash (SHA-256) do estado e das perguntas; o estado em si não é gravado, só as respostas (que podem conter trechos extraídos dele) |
| Gastos e contagem de chamadas por mês | `jev_claud.sqlite3`, tabela `ledger` | Respeitar o orçamento mensal |
| Contadores de economia por mês | `jev_claud.sqlite3`, tabelas `routing` e `savings` | A linha "economizou este mês" e o `jev-claud spend` |
| Saída completa dos testes e builds compactados | `logs/`, só as 20 mais recentes | Consultar o que o resumo omitiu |

Também ficam no seu computador, fora da pasta de dados, o que o `jev-claud setup` grava, sempre
depois de perguntar:

- **Regra global:** um bloco entre marcadores no `~/.claude/CLAUDE.md`.
- **Hook de compactação:** uma entrada no `~/.claude/settings.json`.
- **Servidor MCP:** o registro do `jev-claud` no Claude Code (`claude mcp add`).
- **Atalhos:** os comandos `jev-claud` e `jev-claude` em `~/.local/bin`.

## O que pode sair do seu computador, e quando

| Para onde | Quando | O que vai |
| --- | --- | --- |
| API do TypeSafe Jev (`api.typesafe.ai`) | Só com uma chave configurada **e** orçamento acima de US$ 0 (o padrão é US$ 0) | O estado e as perguntas de cada decisão que vai ao Jev, inclusive imagens se você usar `Image`. Em modo cascata, só as perguntas que o modo offline não resolveu. Segue a política de privacidade da TypeSafe |
| Ollama | Ao usar `write`, `summarize_file` ou o MCP `write` | O texto da instrução ou do arquivo. Por padrão o Ollama roda no seu computador (`localhost:11434`); se você apontar `OLLAMA_HOST` para outra máquina, o texto vai para ela |
| LLM compatível com a API da OpenAI | Só se você definir `JEV_CLAUD_LLM_BASE_URL` | O mesmo texto acima, para o serviço que você escolheu, com a política dele |
| PyPI, winget, Homebrew, ollama.com, claude.ai, GitHub | Durante a instalação, a atualização e o `jev-claud setup`, depois de perguntar | Os pedidos normais de download de cada instalador |

O hook de compactação, o `jev-claud run` e o modo offline não acessam a rede.

## Seus dados, seu controle

- **Desligar a compactação:** `JEV_CLAUD_COMPACT=0`.
- **Desligar o cache:** `JEV_CLAUD_CACHE_TTL_DAYS=0`.
- **Apagar tudo o que o jev-claud guardou:** apague a pasta de dados descrita acima.
- **Desfazer o que o setup configurou:**
  - remova o bloco `jev-claud` do `~/.claude/CLAUDE.md`;
  - remova a entrada `jev_claud hook pre-bash` do `~/.claude/settings.json`;
  - rode `claude mcp remove -s user jev-claud`.
- **Desinstalar:**
  - o plugin: `claude plugin uninstall jevin@jev-claud`;
  - o pacote: `pip uninstall jev-claud`, ou apague `~/.local/share/jev-claud` se usou o instalador.

## Mudanças nesta política

Mudanças nesta política entram no histórico deste arquivo e no [CHANGELOG](CHANGELOG.md).

## Contato

Dúvidas sobre privacidade: abra uma [issue](https://github.com/thiago-lage/jev-claud/issues).
Vulnerabilidades: use o aviso privado descrito em [SECURITY.md](SECURITY.md).
