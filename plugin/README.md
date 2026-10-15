# Jevin

O Jevin é o mascote do [jev-claud](https://github.com/thiago-lage/jev-claud): um robozinho em
pixel art com a antena em forquilha, a bifurcação que o código toma em cada decisão.

Ensina o Claude Code a poupar contexto e custo sem mudar a qualidade da entrega: o Claude
continua implementando direto (edita arquivos, roda testes).

## O que vem no plugin

| Peça | O que faz | Onde funciona |
| --- | --- | --- |
| Skill `jev` | Ensina quando usar decisões tipadas (TypeSafe Jev), texto de LLM ou código puro, quando vale processar um arquivo grande fora do contexto e como ler saídas compactadas. Só entra no contexto quando é relevante. | Claude Code, Cowork, chat |
| Subagente `scout` | Explora o código em contexto separado, somente leitura, e devolve só a conclusão com `caminho:linha`. | Claude Code, Cowork |

## Instalar

No Claude Code 2.1.275 ou mais novo:

```text
/plugin install jevin --marketplace thiago-lage/jev-claud
```

Em versões anteriores:

```text
/plugin marketplace add thiago-lage/jev-claud
/plugin install jevin@jev-claud
```

## Com o pacote jev-claud (opcional)

O pacote Python [`jev-claud`](https://pypi.org/project/jev-claud/) completa o plugin:

```text
pip install jev-claud
jev-claud setup
```

O `setup` registra as ferramentas MCP do jev-claud e um hook que compacta as saídas longas de
testes e builds antes que entrem no contexto. Num teste real (`pytest -v` em um projeto com 204
testes), 214 linhas de saída viraram 65, com o resumo final intacto. Sem o pacote, a skill e o
subagente funcionam normalmente.

## O que o plugin roda, grava e envia

- **Roda:** nada. O plugin tem só texto: a skill e o subagente, em Markdown.
- **Grava:** nada.
- **Envia:** nada. O plugin não acessa a rede e não coleta telemetria.

O que o pacote `jev-claud` roda e grava, se você instalá-lo, está descrito em
<https://github.com/thiago-lage/jev-claud#o-que-o-jev-claud-roda-e-envia>.

## Mais informações

Documentação completa, biblioteca Python e código-fonte:
<https://github.com/thiago-lage/jev-claud>. Problemas e sugestões:
<https://github.com/thiago-lage/jev-claud/issues>.

Licença MIT.
