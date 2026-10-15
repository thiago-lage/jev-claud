# Jevin

O Jevin é o mascote do [jev-claud](https://github.com/thiago-lage/jev-claud): um robozinho em
pixel art com a antena em forquilha, a bifurcação que o código toma em cada decisão.

Este plugin ensina o Claude Code a poupar contexto e custo **sem mudar a qualidade da
entrega**: o Claude continua implementando direto (edita arquivos, roda testes), só que gasta
menos tokens explorando e escolhe a ferramenta certa quando o código usa IA.

## O que vem no plugin

| Peça | O que faz | Onde funciona |
| --- | --- | --- |
| Skill `jev` | Ensina quando usar decisões tipadas (TypeSafe Jev), texto de LLM ou código puro, quando vale processar um arquivo grande fora do contexto e como ler saídas compactadas. Só entra no contexto quando é relevante. | Claude Code, Cowork, chat |
| Subagente `scout` | Explora o código em contexto separado, somente leitura, e devolve só a conclusão com `caminho:linha`. | Claude Code, Cowork |

O plugin é só texto: não roda programas, não grava arquivos e não acessa a rede.

## Instalar

**Dentro do Claude** (diretório de plugins):

1. No claude.ai ou no app desktop, abra **Personalização › Skills** (em inglês, *Customize ›
   Skills*), vá na aba **Descobrir**, procure **jevin** e adicione o **Jevin**.

   ![Tela Skills do Claude com a busca jevin: o plugin Jevin, por Thiago Lage](https://raw.githubusercontent.com/thiago-lage/jev-claud/main/docs/claude-skills.jpg)
2. Pronto: o plugin fica na sua conta e vale no Cowork e nas conversas na web, no desktop e no
   celular. O Claude Code (2.1.273 ou mais novo, entrando com a conta do claude.ai) o sincroniza
   sozinho ao abrir, com o nome `jevin@synced`.

**Pelo marketplace do projeto** (já funciona; só no Claude Code). No 2.1.275 ou mais novo:

```text
/plugin install jevin --marketplace thiago-lage/jev-claud
```

Em versões anteriores: `/plugin marketplace add thiago-lage/jev-claud` e depois
`/plugin install jevin@jev-claud`.

Use um caminho só. Com o Jevin do diretório ativo, a cópia do marketplace impede que ele carregue;
o `jev-claud setup` reconhece o `jevin@synced` e remove a cópia duplicada.

Abra uma sessão nova do Claude Code depois de instalar.

## Como usar

Não precisa chamar nada: o Claude usa a skill e o subagente quando o pedido combina com eles.

**Skill `jev`.** Entra quando o código que você pede usa IA, ou quando há arquivos de dados
grandes para classificar, ranquear ou resumir. Exemplos de pedidos:

- "Classifique os tickets de suporte por setor antes de abrir o chamado": o Claude usa uma
  decisão tipada (`choice`) do Jev em vez de pedir JSON a uma LLM e fazer parse.
- "Escreva a resposta ao cliente com base no ticket": texto livre de LLM, porque uma pessoa vai
  ler.
- "Quantos dos 2.000 tickets de `tickets.csv` são cobrança?": com as ferramentas MCP do
  jev-claud, o Claude envia só o caminho do arquivo e recebe as contagens.

**Subagente `scout`.** Entra em perguntas amplas sobre o código, que exigiriam ler muitos
arquivos. Também dá para pedir direto:

```text
Use o scout para descobrir onde a autenticação é validada e quem chama essa função.
```

O scout procura com `Grep` e `Glob`, lê só os trechos relevantes e devolve até ~40 linhas: a
resposta direta, a lista de `caminho:linha` e os pontos de atenção. Ele usa as ferramentas do
próprio Claude Code (com as mesmas permissões) e é instruído a não editar, instalar nem acessar
a rede. Roda num modelo menor, então a exploração custa menos que no contexto principal.

## Com o pacote jev-claud (opcional)

O pacote Python [`jev-claud`](https://pypi.org/project/jev-claud/) completa o plugin:

```text
pip install jev-claud
jev-claud setup
```

O `setup` pergunta antes de cada passo e pode:

- **Compactar as saídas de testes e builds:** grava um hook no `~/.claude/settings.json`. Em
  saídas acima de ~80 linhas, o Claude recebe o começo, o fim e as linhas de erro. Num teste
  real (`pytest -v` com 204 testes), 214 linhas viraram 65, com o resumo final intacto. Para
  desligar, defina `JEV_CLAUD_COMPACT=0`.
- **Registrar as ferramentas MCP** `classify`, `rank`, `summarize_file`, `decide` e `write`.
  Elas processam arquivos grandes fora do contexto do Claude.
- **Instalar o Ollama**, para texto grátis no seu computador.

Sem o pacote, a skill e o subagente funcionam normalmente.

## Privacidade e dados

O plugin não lê nem guarda dados pessoais e não envia nada por conta própria. O autor não tem
servidor e não recebe nenhum dado: nada é retido fora do seu computador.

A skill `jev` pode orientar o Claude a usar as ferramentas MCP do jev-claud, **se você instalou
o pacote e rodou o `jev-claud setup`**. Só nesse caso, e só com o que o pedido envolver, dados
podem sair do computador para estes serviços:

| Serviço | Quando |
| --- | --- |
| API do TypeSafe Jev (`api.typesafe.ai`) | Só com uma chave configurada e orçamento acima de US$ 0. O padrão é US$ 0, sem envio |
| Ollama | Ao gerar ou resumir texto. Roda no seu computador por padrão (`localhost:11434`) |
| Uma LLM compatível com a API da OpenAI | Só se você definir `JEV_CLAUD_LLM_BASE_URL` |

Detalhes completos, inclusive o que fica no seu computador e como apagar tudo:
[política de privacidade](https://github.com/thiago-lage/jev-claud/blob/main/PRIVACY.md).

## Problemas e suporte

- **O Claude não usa o scout ou a skill:** confira se o plugin aparece em `/plugin` e abra uma
  sessão nova. Pedidos explícitos ("use o scout para...") sempre funcionam.
- **O plugin aparece com o nome antigo (`jev` ou `jev-claude`):** rode `jev-claud setup`, ou
  desinstale o antigo e instale `jevin@jev-claud`.
- **O `jevin@synced` aparece como "not loaded":** há também a cópia do marketplace ativa. Rode
  `claude plugin uninstall jevin@jev-claud` (ou `jev-claud setup`) e abra uma sessão nova.
- **Outros problemas:** veja o
  [guia de suporte](https://github.com/thiago-lage/jev-claud/blob/main/SUPPORT.md) ou
  [abra uma issue](https://github.com/thiago-lage/jev-claud/issues/new/choose).

## Desinstalar

- **Pelo diretório:** remova o Jevin em **Personalização › Skills** no claude.ai. Para só
  desligar no Claude Code: `claude plugin disable jevin@synced`.
- **Pelo marketplace:** `/plugin uninstall jevin@jev-claud` (ou, no terminal,
  `claude plugin uninstall jevin@jev-claud`).

## Mais informações

- Projeto, biblioteca Python e código-fonte: <https://github.com/thiago-lage/jev-claud>
- Mudanças por versão: [CHANGELOG](https://github.com/thiago-lage/jev-claud/blob/main/CHANGELOG.md)

Licença MIT.
