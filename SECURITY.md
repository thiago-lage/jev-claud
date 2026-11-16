# Segurança

## Reportando vulnerabilidades

Não abra uma issue pública. Use o recurso **Security > Report a vulnerability** do GitHub
(aviso privado) com uma descrição e, se possível, um exemplo mínimo. A resposta inicial sai
em até 7 dias.

## Como conferir que você tem a versão oficial

Qualquer pessoa pode fazer um fork e modificar a cópia dela (a licença MIT permite). A versão
oficial é só a que sai deste repositório, `thiago-lage/jev-claud`, e do projeto
[`jev-claud` no PyPI](https://pypi.org/project/jev-claud/).

- **Só o dono altera o repositório oficial.** A `main` não aceita force-push nem exclusão; as tags
  `v*` não podem ser movidas nem apagadas; e os releases são imutáveis: depois de publicados,
  tag e arquivos não mudam mais.
- **O pacote no PyPI é publicado pelo workflow `release.yml`**, por trusted publishing (sem token
  guardado) e com atestado de proveniência. A página do pacote no PyPI mostra o repositório e o
  workflow de origem em *Verified details*.
- **O wheel anexado ao release tem atestado de build.** Com o GitHub CLI:

  ```bash
  gh release verify v1.0.1 --repo thiago-lage/jev-claud
  gh attestation verify jev_claud-1.0.1-py3-none-any.whl --repo thiago-lage/jev-claud
  ```

  (Releases até a 1.0.0 saíram antes dessas proteções: não têm atestado nem são imutáveis.)
- **Instalador numa versão fixa.** O instalador de uma linha usa o `install.sh`/`install.ps1` da
  `main` e instala a versão mais nova do PyPI. Para fixar os dois, use a tag da versão:

  ```bash
  curl -fsSL https://raw.githubusercontent.com/thiago-lage/jev-claud/v1.0.1/install.sh \
    | JEV_CLAUD_SPEC="jev-claud[jev]==1.0.1" bash
  ```

  No Windows: `$env:JEV_CLAUD_SPEC = "jev-claud[jev]==1.0.1"` e depois
  `irm https://raw.githubusercontent.com/thiago-lage/jev-claud/v1.0.1/install.ps1 | iex`.
- **Desconfie de pacotes com nomes parecidos** (`jev-claude`, `jevclaud`...) no PyPI ou de
  cópias no GitHub: o pacote oficial é só `jev-claud`. (O comando `jev-claude` que o instalador
  cria é só um atalho antigo para o mesmo programa.)

## Boas práticas ao usar o jev-claud

- **Chaves ficam no servidor.** `TYPESAFE_API_KEY` e a chave de um `Writer` que você passar à
  biblioteca nunca devem ir para o navegador ou para o repositório. Use variáveis de ambiente ou um cofre de segredos.
- **O `jev-claud setup` guarda a chave do Jev em `config.json`** (em `JEV_CLAUD_HOME`), com
  permissão só para o seu usuário. Em servidores, prefira a variável `TYPESAFE_API_KEY`.
- **Os instaladores** baixam apenas de fontes oficiais (PyPI, GitHub deste repositório, winget,
  Homebrew, claude.ai) e o assistente pergunta antes de cada instalação.
- **O cache guarda as respostas** em SQLite local (`JEV_CLAUD_HOME`), com um hash do estado
  como chave. As respostas podem conter trechos extraídos do estado: se ele tiver dados pessoais, proteja o diretório ou desligue o cache (`JEV_CLAUD_CACHE_TTL_DAYS=0`).
- **Saída tipada garante a interface, não a verdade.** Decisões com impacto (pagamentos,
  exclusões, acesso) devem exigir confiança alta (`Policy`) e, quando possível, revisão humana.

O que o jev-claud guarda e envia está descrito na [política de privacidade](PRIVACY.md).
- O SDK do TypeSafe registra corpos de requisição no log em nível debug; não ative
  `TYPESAFE_LOG_LEVEL=debug` em produção com dados sensíveis.
