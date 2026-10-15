---
name: jev
description: Quando o código que você escreve usa IA (decisão tipada com TypeSafe Jev vs. texto com LLM), ou quando há arquivos de dados grandes (CSV, logs, listas, relatórios) para classificar, ranquear ou resumir sem lê-los no contexto. Também para saídas de teste/build compactadas pelo jev-claud.
---

# Jev: decisões tipadas, texto barato e contexto enxuto

Esta skill **não muda como você trabalha**: pedidos de mudança em código você implementa
direto, editando os arquivos e rodando os testes. Nunca delegue código ao Ollama ou ao Jev.

## 1. Quando o produto usa IA

| A saída da IA... | Use | Como |
| --- | --- | --- |
| é consumida pelo código para ramificar (if/switch, rota, filtro, ranking, validação) | **TypeSafe Jev** | `from jev_claud import JevClaud`; `r.decide/when/switch/rank` com `choice`, `noul`, `score` |
| é lida por uma pessoa (e-mail, resposta, resumo) | **LLM** | `r.write(...)` (Ollama local por padrão, grátis) |
| segue regra conhecida, cálculo ou busca exata | **código puro** | sem IA |

Não peça JSON a uma LLM para depois fazer parse: isso é o que o Jev resolve.

Com orçamento, o padrão é a **cascata**: o offline grátis responde o óbvio e só o incerto vai
ao Jev (`d.source == "mixed"`, `d.escalated`). Descreva cada opção com os termos reais dos dados:
mais perguntas saem de graça. Imagens: `Image("foto.jpg")` no estado e `box("Onde está X?")`
para localizar algo (exigem o Jev com modelo de visão).

O Jev é pago por uso; o padrão é orçamento US$ 0 (modo offline grátis). Ao integrar,
leia https://docs.typesafe.ai/llms.txt e confira os tipos reais em `typesafe_sdk`.

## 2. Poupar contexto com as ferramentas MCP `jev-claud`

Essas ferramentas não vêm no plugin: existem quando o `jev-claud setup` registrou o servidor MCP
`jev-claud`. Sem elas, pule esta seção. Use quando o volume é de **dados**, não de código que
você vai editar:

- `classify` / `rank`: muitos itens (um por linha) em um arquivo -> passe o CAMINHO.
  Devolve contagens e rótulos; os itens não entram no seu contexto.
- `summarize_file`: arquivo de texto grande (log, relatório, transcrição) -> resumo.
  O resumo vem de um modelo local pequeno: confira o trecho original antes de afirmar
  algo crítico com base nele.
- `decide`: uma decisão tipada pontual.
- `status`: o que está ativo (Jev, Ollama, cache).

Não use para: código-fonte que você vai alterar (leia-o você mesmo), perguntas pequenas
e únicas (responder direto custa menos) ou gerar código (nunca).

## 3. Saídas compactadas

Com o hook do `jev-claud setup`, testes, builds e linters simples (`pytest`, `npm test`,
`cargo build`...) rodam por `jev-claud run`. Saídas acima de ~80 linhas chegam com o começo, o fim e as linhas
de erro com contexto; o rodapé `[jev-claud] saída compactada: ... completa em <caminho>`
indica onde está a saída inteira. O código de saída é o do comando original.

- Precisa de algo que foi omitido? Leia o arquivo do rodapé (com `offset`/`limit`, ou `grep`).
- Precisa da saída crua? Rode com `JEV_CLAUD_COMPACT=0` na frente do comando.

## 4. Explorar sem encher o contexto

Para perguntas amplas sobre o código ("onde X é tratado?", "como Y funciona?") que exigiriam
ler muitos arquivos, delegue ao subagente `jevin:scout`: só a conclusão volta.
