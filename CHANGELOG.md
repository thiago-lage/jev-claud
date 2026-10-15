# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/); versões seguem
[SemVer](https://semver.org/lang/pt-BR/).

## [0.9.0] - 2026-10-15

### Adicionado

- **Cursor e Codex no `jev-claud setup`.** Quando estão instalados, o setup oferece configurá-los:
  - Cursor: o servidor MCP do jev-claud no `~/.cursor/mcp.json`.
  - Codex: o servidor MCP na tabela `[mcp_servers.jev-claud]` do `config.toml` e a regra global
    no `AGENTS.md`, ou no `AGENTS.override.md`, se ele estiver em uso. Respeita o `$CODEX_HOME`.
  - Em todos os casos o setup troca só a entrada do jev-claud e preserva o resto do arquivo. O TOML
    é conferido antes de gravar, e um arquivo que não dá para ler com segurança fica como está.
- `jev-claud rule` mostra a regra "Jev ramifica, LLM escreve", para colar onde não há arquivo de
  regras globais (como em *Cursor Settings › Rules*).

### Alterado

- Regra global na versão 4, com texto que vale para qualquer agente. O `setup` atualiza o bloco no
  `~/.claude/CLAUDE.md`.

## [0.8.2] - 2026-10-15

### Alterado

- **Jevin pelo diretório de plugins do Claude.** O `jev-claud setup` reconhece o plugin adicionado
  pelo claude.ai (`jevin@synced`) e não instala a cópia do marketplace. Se as duas estiverem
  ativas, ele remove a do marketplace, que impediria a do diretório de carregar.
- READMEs e suporte explicam os dois caminhos de instalação: pelo diretório (**Personalização ›
  Plugins** no claude.ai, depois da aprovação) e pelo marketplace do projeto.

### Documentação

- Páginas próprias para a ficha do plugin no diretório da Anthropic:
  - `plugin/README.md` virou o guia completo do Jevin (`documentationUrl`);
  - `SUPPORT.md`, com problemas comuns e formulários de issue (`supportUrl`);
  - `PRIVACY.md`, a política de privacidade (`privacyPolicyUrl`).

## [0.8.1] - 2026-10-15

### Alterado

- **O instalador de uma linha e o `jev-claud update` usam a versão lançada no PyPI**
  (`jev-claud[jev]`), e não mais o que estiver no GitHub. A versão em desenvolvimento continua
  disponível com `jev-claud update --dev` ou `JEV_CLAUD_SPEC` no instalador.

## [0.8.0] - 2026-10-15

### Alterado

- **O hook que compacta as saídas de testes e builds saiu do plugin.** Agora o `jev-claud setup`
  o grava no `~/.claude/settings.json` (passo "Saídas de teste compactadas"), chamando o Python do
  jev-claud pelo caminho absoluto. O setup preserva o resto do arquivo, troca entradas antigas do
  próprio hook e não mexe em um `settings.json` inválido.
- **O plugin Jevin tem só texto** (a skill `jev` e o subagente `scout`): não roda nada, então o
  diretório da Anthropic o valida sem revisão manual de scripts.
- Regra global na versão 3: cita o hook do jev-claud, não o do plugin.
- O selo do PyPI no README usa um endereço novo, sem o "not found" guardado em cache de antes da
  primeira publicação.

### Atualizar

- Rode `jev-claud setup` depois de atualizar: sem isso, a compactação para quando o plugin for
  atualizado para a 0.8. No Cowork, o hook não vem mais pelo plugin.

## [0.7.1] - 2026-10-15

### Corrigido

- A descrição do plugin Jevin e a skill `jev` deixam claro que as ferramentas MCP vêm do
  `jev-claud setup`, não do plugin. A skill cita o subcomando certo (`jev-claud run`), e os READMEs
  mostram a instalação em um passo: `/plugin install jevin --marketplace thiago-lage/jev-claud`.

## [0.7.0] - 2026-10-15

### Alterado

- **O projeto passa a se chamar `jev-claud`**: pacote (`pip install jev-claud`), comando
  `jev-claud`, módulo `jev_claud`, classe `JevClaud`, variáveis `JEV_CLAUD_*`, repositório
  `thiago-lage/jev-claud` e marketplace `jev-claud`.
- **O plugin passa a se chamar Jevin** (`jevin@jev-claud`), como o mascote.
- **Jevin, o mascote**, tem agora as proporções do bonequinho do Claude Code (olhos, bracinhos e
  quatro perninhas, em blocos de quadrante), com a antena em forquilha. Ícone e imagens novos.
- O hook do plugin aponta direto para `hooks/pre-bash.sh`, como pede o diretório da Anthropic.

### Compatibilidade (nada do que já estava instalado quebra)

- `jev-claude`, `python -m jev_claude`, `from jev_claude import JevClaude` e `JevClaudeError`
  continuam funcionando como atalhos para o nome novo.
- Variáveis `JEV_CLAUDE_*` valem quando a `JEV_CLAUD_*` correspondente não está definida.
- A pasta de dados `jev-claude` (chave do Jev, cache, gastos) e o banco `jev_claude.sqlite3` são
  renomeados sozinhos na primeira execução.
- `jev-claud setup` troca o MCP `jev-claude`, o marketplace `jev-claude` e os plugins
  `jev-claude`/`jev` pelos novos, e atualiza a regra global com os marcadores antigos.
- Quem tem a 0.6: o `jev-claude update` antigo não consegue trocar de pacote; rode o instalador de
  novo (ele cria os comandos `jev-claud` e `jev-claude`) e depois `jev-claud setup`.

## [0.6.2] - 2026-10-15

### Alterado

- O plugin foi para a pasta `plugin/` (com README e licença próprios): o diretório da Anthropic
  analisa só o plugin, não a biblioteca e os instaladores. O marketplace aponta para
  `./plugin`; quem já tinha o plugin instalado continua funcionando.
- Nome de exibição do plugin: "Jev Claude". O identificador continua `jev`.

## [0.6.1] - 2026-10-15

### Alterado

- O plugin do Claude Code passa a se chamar **`jev`** (exibido como "Jev"), para o diretório de
  plugins da Anthropic, que não aceita nomes que lembrem a marca. O pacote e o comando continuam
  `jev-claude`. O marketplace migra quem tinha `jev-claude@jev-claude` (`renames`), e o
  `jev-claude setup` instala o `jev@jev-claude` e remove o antigo.
- O hook roda por `hooks/pre-bash.sh`, que não faz nada se o `jev-claude` não estiver instalado
  (antes, um erro a cada comando).
- Manifesto com ícone (o mascote), links de documentação, suporte e privacidade. README descreve
  tudo o que o plugin roda, grava e envia. `CLAUDE.md` do projeto foi para `.claude/CLAUDE.md`.

## [0.6.0] - 2026-10-15

### Adicionado

- Mascote do jev-claude no terminal: um robozinho em pixel art com a antena em forquilha
  (a bifurcação que o Jev decide), na caixa do `setup` e na nova tela do `jev-claude` sem
  argumentos, que lista os comandos principais (`-h` continua com a ajuda completa).
  Terminais sem suporte aos blocos recebem uma versão em ASCII.
- Economia de tokens em cinza, logo abaixo do mascote e no fim do `doctor` e do `spend`:
  tokens do Claude poupados (saídas compactadas pelo hook e arquivos processados pelas
  ferramentas MCP em vez de lidos) e tokens do Jev poupados (cascata e cache), no mês.

## [0.5.0] - 2026-10-15

### Adicionado

- **Cascata** (estratégia padrão com o Jev ligado): cache -> offline grátis -> Jev só para as
  perguntas que o offline não resolveu com confiança (`JEV_CLAUDE_CASCADE_ACCEPT`, 0,8), numa
  única requisição. `Decisions.source` ganha `"mixed"`, `Decisions.escalated` diz o que foi ao
  Jev e cada `Decision.source` diz de onde veio. `rank` nunca mistura offline e Jev.
  `JEV_CLAUDE_STRATEGY=jev` mantém o comportamento anterior.
- Relatório de roteamento: `jev-claude spend` e `doctor` mostram quantas perguntas cada camada
  respondeu e os tokens do Jev economizados.
- **Imagens** (System One com visão): `Image(caminho | bytes)` no estado, em listas ou dentro
  de textos, enviadas como assets do `typesafe-sdk`; referência estável por conteúdo (o cache
  funciona). Pergunta **`Box`** (`box(...)`) devolve uma `Region` (bordas em frações,
  `to_pixels`, `area`, `center`). CLI: `ask --image --box`; MCP: `decide` com `images` e `box`.
- `Decision.number`: o valor numérico de qualquer decisão não-box.

## [0.4.0] - 2026-10-15

### Adicionado

- Plugin do Claude Code (`/plugin install jev-claude@jev-claude`, o repositório é o próprio
  marketplace): hook que compacta saídas de testes/builds, skill `jev` e subagente `scout`.
- `jev-claude run -- <comando>`: roda um teste/build, compacta a saída sem LLM, guarda a saída
  completa em `logs/` e sai com o código do comando. Recusa comandos que não sejam teste/build/lint.
- `jev-claude compact` (filtro de texto) e `jev-claude hook pre-bash` (usado pelo plugin).
- `jev-claude setup` instala o plugin e, no Windows, cria o atalho `jev-claude` para o Git Bash.

### Corrigido

- A regra global podia levar o Claude Code a devolver código no chat ou delegar "rascunhos" ao
  Ollama em vez de implementar. A regra (versão 2) e as instruções do MCP agora dizem para
  implementar direto e nunca usar o Ollama/Jev para código. O `setup` substitui a versão antiga.

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
