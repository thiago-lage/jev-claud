# Jevin

O Jevin é o mascote do [jev-claud](https://github.com/thiago-lage/jev-claud): um robozinho em
pixel art com a antena em forquilha, a bifurcação que o código toma em cada decisão.

Este plugin orienta o Claude Code a investigar com evidências e reduzir contexto.
O Claude planeja, lê as fontes, edita e valida; jev-claud prepara dados e sinais de falha.
Resumos locais precisam de conferência e não garantem a mesma qualidade do original.

Na versão em desenvolvimento, a skill inclui o fluxo teste → `diagnose_log` →
`read_evidence` → correção pelo agente → nova execução. Exige a versão correspondente
do servidor MCP. Só instalar a skill não adiciona essas ferramentas.

## O que vem no plugin

| Peça | O que faz | Onde funciona |
| --- | --- | --- |
| Skill `jev` | Ensina quando usar decisões tipadas (TypeSafe Jev), texto de LLM ou código puro, quando vale processar um arquivo grande fora do contexto e como ler saídas compactadas. Só entra no contexto quando é relevante. | Claude Code, Cowork, chat |
| Subagente `scout` | Explora o código em contexto separado, somente leitura, e devolve só a conclusão com `caminho:linha`. | Claude Code, Cowork |
| Servidor MCP `jev-claud` | Dá ao Claude as ferramentas `classify`, `rank`, `summarize_file`, `decide`, `write` e as de diagnóstico, que processam arquivos grandes fora do contexto. Precisa do [pacote jev-claud](#com-o-pacote-jev-claud) instalado. | Claude Code, Cowork (tarefas no seu computador) |

A skill e o subagente são só texto. O plugin não traz código: o servidor MCP é o comando
`jev-claud mcp` do pacote que você instalou. Sem o pacote, ele aparece como falho em `/mcp` e o
resto funciona normalmente.

## Instalar

O Jevin ainda não está no diretório de plugins do Claude: procurá-lo em **Descobrir** sem
adicionar o marketplace não acha nada. Instale pelo marketplace do projeto.

**Na sua conta do Claude** (Cowork e conversas): no app desktop ou no claude.ai, abra
**Personalização › Plugins** (*Customize › Plugins*), clique em **Adicionar › Adicionar
marketplace**, informe `thiago-lage/jev-claud` e instale o **Jevin**. No Cowork, comece uma tarefa
nova para ele carregar. Plugins instalados pelo Claude Code ficam só naquele computador e não
aparecem aqui.

**No Claude Code.** No 2.1.275 ou mais novo:

```text
/plugin install jevin --marketplace thiago-lage/jev-claud
```

Em versões anteriores: `/plugin marketplace add thiago-lage/jev-claud` e depois
`/plugin install jevin@jev-claud`.

Se o Claude Code também receber a cópia da sua conta (`jevin@synced`), a do marketplace impede
que ela carregue; o `jev-claud setup` reconhece o `jevin@synced` e remove a cópia duplicada.

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
- **Ligar as ferramentas MCP** `classify`, `rank`, `summarize_file`, `decide` e `write`, que
  processam arquivos grandes fora do contexto do Claude. Com o plugin, o servidor já vem nele;
  o `setup` só registra outro se o do plugin não conectar, e remove o registro repetido.
  O texto dessas ferramentas fica com o LLM da sessão: por MCP sampling quando o host oferece;
  senão, o trabalho volta ao próprio agente (`mode: "agent"`). Não há modelo local para instalar.

Sem o pacote, a skill e o subagente funcionam normalmente.

## Privacidade e dados

O plugin não lê nem guarda dados pessoais e não envia nada por conta própria. O autor não tem
servidor e não recebe nenhum dado: nada é retido fora do seu computador.

A skill `jev` pode orientar o Claude a usar as ferramentas MCP do jev-claud, que só funcionam
**se você instalou o pacote**. Só nesse caso, e só com o que o pedido envolver, dados
podem sair do computador para estes serviços:

| Serviço | Quando |
| --- | --- |
| API do TypeSafe Jev (`api.typesafe.ai`) | Só com uma chave configurada e orçamento acima de US$ 0. O padrão é US$ 0, sem envio |
| O LLM da sessão (o host do MCP) | Ao gerar ou resumir texto e nos itens incertos, só se o host oferecer MCP sampling. Sem sampling, nada sai: o trabalho volta ao agente |

Detalhes completos, inclusive o que fica no seu computador e como apagar tudo:
[política de privacidade](https://github.com/thiago-lage/jev-claud/blob/main/PRIVACY.md).

## Problemas e suporte

- **O Claude não usa o scout ou a skill:** confira se o plugin aparece em `/plugin` e abra uma
  sessão nova. Pedidos explícitos ("use o scout para...") sempre funcionam.
- **O plugin aparece com o nome antigo (`jev` ou `jev-claude`):** rode `jev-claud setup`, ou
  desinstale o antigo e instale `jevin@jev-claud`.
- **O servidor `plugin:jevin:jev-claud` aparece como falho:** o comando `jev-claud` não está no
  PATH. Instale o pacote pelo `install.ps1`/`install.sh` (ele põe o comando no PATH) e abra uma
  sessão nova.
- **O `jevin@synced` aparece como "not loaded":** há também a cópia do marketplace ativa. Rode
  `claude plugin uninstall jevin@jev-claud` (ou `jev-claud setup`) e abra uma sessão nova.
- **Outros problemas:** veja o
  [guia de suporte](https://github.com/thiago-lage/jev-claud/blob/main/SUPPORT.md) ou
  [abra uma issue](https://github.com/thiago-lage/jev-claud/issues/new/choose).

## Desinstalar

- **Na sua conta do Claude:** remova o Jevin em **Personalização › Plugins**. Para só desligar
  a cópia sincronizada no Claude Code: `claude plugin disable jevin@synced`.
- **No Claude Code:** `/plugin uninstall jevin@jev-claud` (ou, no terminal,
  `claude plugin uninstall jevin@jev-claud`).

## Mais informações

- Projeto, biblioteca Python e código-fonte: <https://github.com/thiago-lage/jev-claud>
- Mudanças por versão: [CHANGELOG](https://github.com/thiago-lage/jev-claud/blob/main/CHANGELOG.md)

Licença MIT.
