# Jevin

O Jevin é o mascote do [jev-claud](https://github.com/thiago-lage/jev-claud): um robozinho em
pixel art com a antena em forquilha, a bifurcação que o código toma em cada decisão.

Poupa contexto e custo no Claude Code sem mudar a qualidade da entrega: o Claude continua
implementando direto (edita arquivos, roda testes), e o plugin só tira o ruído do caminho.

## O que vem no plugin

| Peça | O que faz | Onde funciona |
| --- | --- | --- |
| Hook `PreToolUse` | Testes, builds e linters simples (`pytest`, `npm test`, `cargo build`, `go test`, `make`...) chegam ao Claude com a saída compactada: o começo, o fim e as linhas de erro com contexto. A saída completa fica num arquivo local citado no rodapé, e o código de saída é o do comando original. | Claude Code, Cowork |
| Skill `jev` | Ensina quando usar decisões tipadas (TypeSafe Jev), texto de LLM ou código puro, e quando vale processar um arquivo grande fora do contexto. Só entra no contexto quando é relevante. | Claude Code, Cowork, chat |
| Subagente `scout` | Explora o código em contexto separado, somente leitura, e devolve só a conclusão com `caminho:linha`. | Claude Code, Cowork |

Num teste real (`pytest -v` em um projeto com 204 testes), 214 linhas de saída viraram 65, com o
resumo final intacto.

## Requisito

O hook usa o pacote Python [`jev-claud`](https://pypi.org/project/jev-claud/), instalado com
`pip install jev-claud`. Sem ele, o hook não faz nada e a skill e o subagente funcionam
normalmente.

## O que o plugin roda, grava e envia

- **Roda:** a cada comando que o Claude Code vai executar no Bash, `hooks/pre-bash.sh` chama
  `jev-claud hook pre-bash`. Se o comando for um teste, build ou lint simples, ele é reescrito
  para `python -m jev_claud run -- <o mesmo comando>`, que o executa, compacta a saída e devolve
  o mesmo código de saída. Comandos compostos (com `|`, `>`, `&&`, `;`) e qualquer outro comando
  ficam como estão.
- **Permissões:** o hook não aprova nada. O comando reescrito passa pelas mesmas perguntas de
  permissão do Claude Code.
- **Grava, só localmente:** a saída completa dos comandos compactados (as 20 mais recentes) e
  contadores de economia por mês, na pasta de dados do `jev-claud`.
- **Envia:** nada. O plugin não acessa a rede e não coleta telemetria.
- **Desligar a compactação:** defina `JEV_CLAUD_COMPACT=0`.

## Mais informações

Documentação completa, biblioteca Python e código-fonte:
<https://github.com/thiago-lage/jev-claud>. Problemas e sugestões:
<https://github.com/thiago-lage/jev-claud/issues>.

Licença MIT.
