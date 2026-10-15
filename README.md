# jev-claud

[![PyPI](https://img.shields.io/pypi/v/jev-claud?label=pypi&cacheSeconds=3600)](https://pypi.org/project/jev-claud/)
[![CI](https://github.com/thiago-lage/jev-claud/actions/workflows/ci.yml/badge.svg)](https://github.com/thiago-lage/jev-claud/actions/workflows/ci.yml)

**Decisões tipadas para o código. Texto livre para pessoas. Custo zero por padrão.**

<p align="center">
  <img src="https://raw.githubusercontent.com/thiago-lage/jev-claud/main/docs/terminal.png" alt="Terminal do jev-claud: o mascote em pixel art, os comandos principais e a economia de tokens do mês" width="720">
</p>

`jev-claud` separa as duas saídas de IA que um app tem:

| Quem consome a resposta? | Use | Motor | Por quê |
| --- | --- | --- | --- |
| **O código**, para ramificar (`if`, `match`, roteamento, filtro, ranking) | `decide`, `when`, `switch`, `rank` | [TypeSafe **Jev**](https://docs.typesafe.ai) (System One) | Resposta tipada e calibrada (rótulo, probabilidade, grau). Sem gerar JSON, sem parse, sem tokens de saída pagos. |
| **Uma pessoa**, que vai ler | `write` | LLM (Ollama local, endpoint compatível com OpenAI, ou qualquer função) | Texto natural é o que LLMs fazem bem. |

Regras conhecidas, cálculos e buscas exatas continuam em código puro: a IA entra só onde
falta senso comum semântico.

```python
from jev_claud import JevClaud, Case, choice, noul, score

r = JevClaud()

d = r.decide(
    ticket_text,
    {
        "setor": choice(
            "Qual setor deve atender este ticket?",
            {
                "financeiro": "cobrança, fatura, reembolso, pagamento",
                "suporte": "erro, bug, falha técnica, não funciona",
                "vendas": "compra, plano, preço, upgrade",
            },
        ),
        "urgente": noul("O cliente precisa de ação imediata?"),
        "raiva": score("Quão irritado está o cliente?", ["calmo", "frustrado", "furioso"]),
    },
)

if d["setor"].uncertain:  # confiança baixa -> humano
    enviar_para_humano(ticket_text)
elif d["setor"].is_("financeiro") and d["urgente"].yes:
    abrir_chamado_prioritario()

resposta = r.write("Escreva uma resposta curta e empática para o cliente.", context=ticket_text)
```

## Por que isso economiza

- **Uma requisição, várias decisões.** Perguntas independentes sobre o mesmo estado vão
  juntas em `decide` e rodam em paralelo no Jev: o custo fixo da requisição é pago uma vez.
- **Saída não custa.** O Jev cobra só tokens de entrada (US$ 0,042 por milhão em 2026-10);
  a resposta é um valor tipado, não texto gerado.
- **Cascata.** Com o Jev ligado, cada pergunta passa primeiro pelo modo offline grátis; só as
  que ele não resolve com confiança (padrão: >= 0,8) vão ao Jev, todas numa requisição. O
  óbvio sai de graça, o difícil continua com o Jev. Veja [Cascata](#cascata-pague-só-pelo-difícil).
- **Cache local.** A mesma pergunta sobre o mesmo estado no mesmo modelo sai do SQLite,
  de graça (`source == "cache"`).
- **Orçamento com teto.** Padrão: **US$ 0/mês**. Sem orçamento, tudo roda no backend
  offline gratuito e o log avisa uma vez. Você libera o Jev conscientemente.

> O Jev não tinha plano gratuito documentado quando este projeto foi escrito (2026-10).
> Por isso o padrão é custo zero: o modo offline é uma heurística lexical local, boa para
> desenvolvimento, testes e CI, mas **não substitui o Jev** em produção.

## Instalação rápida (auto-setup)

Uma linha instala tudo num ambiente isolado (sem mexer no seu Python) e abre o assistente:

**Windows** (PowerShell):

```powershell
irm https://raw.githubusercontent.com/thiago-lage/jev-claud/main/install.ps1 | iex
```

**macOS / Linux**:

```bash
curl -fsSL https://raw.githubusercontent.com/thiago-lage/jev-claud/main/install.sh | bash
```

O assistente `jev-claud setup` funciona como o primeiro `claude` no terminal: mostra o que
falta, explica para que serve e **pergunta antes de instalar cada coisa**:

<p align="center">
  <img src="https://raw.githubusercontent.com/thiago-lage/jev-claud/main/docs/setup.png" alt="jev-claud setup: o mascote, a economia do mês e a verificação de cada peça, perguntando antes de instalar" width="720">
</p>

| Passo | O que faz |
| --- | --- |
| Ollama + servidor + modelo | Instala (winget / Homebrew / script oficial), inicia e baixa o `llama3.2` (~2 GB): texto grátis no `write()`. |
| Chave do Jev | Opcional. Guarda a chave e o orçamento mensal em `config.json` (padrão US$ 0). |
| Claude Code | Instalador oficial de claude.ai. |
| Skill TypeSafe | `claude plugin install typesafe@typesafe-ai`. |
| Servidor MCP | Registra o `jev-claud` no Claude Code para delegar lotes e economizar tokens. |
| Saídas compactadas | Grava no `~/.claude/settings.json` o hook que compacta as saídas longas de testes e builds (detalhes abaixo). |
| Plugin | Instala o plugin **Jevin** (`jevin@jev-claud`): skill e subagente explorador. |
| Regra global | Ensina o Claude, no `~/.claude/CLAUDE.md`, quando usar Jev e quando usar LLM. |
| Cursor e Codex | Só se estiverem instalados: as mesmas ferramentas MCP e, no Codex, a mesma regra ([detalhes](#também-no-cursor-e-no-codex)). |

```bash
jev-claud setup            # interativo (também abre sozinho no primeiro `jev-claud`)
jev-claud setup --yes      # aceita tudo que não exige digitar nada
jev-claud setup --check    # só verifica; sai com 1 se faltar algo essencial
jev-claud setup --model qwen2.5:3b   # outro modelo do Ollama
```

### Instalação pelo PyPI

```bash
pip install "jev-claud[jev]"    # com o SDK do Jev
pip install jev-claud           # só o núcleo: nenhuma dependência, modo offline grátis
jev-claud setup                 # opcional: Ollama, Claude Code, plugin e regra global
```

O instalador de uma linha e o `jev-claud update` usam a versão lançada no PyPI. Versão em
desenvolvimento, direto do GitHub: `jev-claud update --dev`, ou
`pip install "jev-claud[jev] @ git+https://github.com/thiago-lage/jev-claud"`.

Sem o extra `[jev]` o pacote não tem nenhuma dependência e roda só no modo offline. Prefira
um ambiente virtual: o `typesafe-sdk` exige versões recentes de `anyio`/`h11`, que podem
conflitar com pacotes antigos no Python global.

Exemplo completo, que roda sem configurar nada: [`examples/triagem_suporte.py`](examples/triagem_suporte.py).

## Dentro do Claude: servidor MCP para economizar tokens

O `jev-claud setup` registra o jev-claud como servidor MCP do Claude Code (ou faça à mão):

```bash
claude mcp add -s user jev-claud -- python -m jev_claud mcp
```

Nas próximas sessões o Claude ganha estas ferramentas, que rodam **fora do contexto dele**:

| Ferramenta | O Claude manda | Recebe de volta | Motor |
| --- | --- | --- | --- |
| `classify` | caminho de um arquivo + rótulos | contagens + itens incertos | Jev / offline |
| `rank` | caminho + consulta | só os top_k | Jev / offline |
| `summarize_file` | caminho + foco | um resumo | Ollama (grátis) |
| `decide` | texto/JSON + perguntas | valores tipados + confiança | Jev / offline |
| `write` | instrução curta | rascunho | Ollama (grátis) |
| `status` | — | o que está ativo e o gasto do mês | — |

**Onde a economia é real:** tudo que o Claude lê ou escreve custa tokens. Se você pede
"classifique os 2.000 tickets de `tickets.csv`", ler o arquivo custaria dezenas de milhares de
tokens. Com `classify`, o Claude envia ~100 tokens (o caminho e os rótulos) e recebe ~200 (as
contagens). O mesmo vale para resumir arquivos grandes e escolher o que vale a pena ler.

**Onde não compensa:** pergunta pequena e única. A chamada da ferramenta e as definições das
ferramentas também custam tokens; o Claude responde direto. E o `llama3.2` (3B) é bem menos
capaz que o Claude: use para rascunhos e resumos, e deixe o Claude revisar o que importa. A
regra global instalada pelo setup orienta o Claude a fazer essa escolha.

## Saídas de teste compactadas

O `jev-claud setup` grava um hook `PreToolUse` no `~/.claude/settings.json`. Testes, builds e
linters simples (`pytest`, `npm test`, `cargo build`, `go test`, `make`...) passam a rodar via
`jev-claud run`: saídas acima de ~80 linhas chegam ao Claude com o começo, o fim e as linhas de
erro com contexto. A saída completa fica num arquivo citado no rodapé, e o código de saída é o
do comando original. Custo zero: não usa LLM.

- **Não aprova nada sozinho**: o comando reescrito passa pelas mesmas permissões. Na primeira
  vez o Claude Code pergunta; escolha "não perguntar de novo" para o `... -m jev_claud run`.
- **Conservador**: só mexe em comandos simples; com `|`, `>`, `&&`, `;`, `$(...)` ou em segundo
  plano, nada muda. O `jev-claud run` recusa qualquer comando que não seja teste/build/lint.
- **Desligar**: `JEV_CLAUD_COMPACT=0` (no ambiente ou na frente de um comando).
- Teste real (este repositório, `pytest -v`): 174 linhas viraram 64, com o resumo final intacto.

O hook usa o caminho absoluto do Python do jev-claud, que funciona em todos os sistemas (no
Windows, o Claude Code roda os comandos no Git Bash). O setup preserva o resto do
`settings.json` e não mexe nele se o arquivo não for um JSON válido.

### O que o jev-claud roda e envia

Transparência total sobre o que acontece na sua máquina:

- **Roda:** o hook chama `python -m jev_claud hook pre-bash` só quando o Claude Code vai rodar
  um comando no Bash. Se for um teste/build/lint simples, o comando é reescrito para
  `python -m jev_claud run -- <o mesmo comando>`, que o executa, compacta a saída e devolve o
  mesmo código de saída. Nenhum outro comando é alterado. O plugin Jevin não roda nada: tem só
  texto (a skill e o subagente).
- **Grava (só localmente, na pasta de dados do jev-claud):** a saída completa de cada comando
  compactado (`logs/`, as 20 mais recentes) e contadores de economia por mês num SQLite. Nada sai
  da sua máquina.
- **Envia:** o hook e o plugin não acessam a rede. A biblioteca `jev-claud` só envia texto para
  a API da TypeSafe (Jev) se você configurar uma chave e um orçamento, e só para o Ollama local
  quando usa a LLM de texto.
- **Não faz:** não lê credenciais, não altera permissões do Claude Code (o comando reescrito
  passa pelas mesmas perguntas de permissão) e não coleta telemetria.

Detalhes completos, inclusive como apagar tudo: [política de privacidade](PRIVACY.md).

## Plugin do Claude Code: Jevin

O plugin ensina o Claude a poupar contexto **sem mudar a qualidade da entrega**: o Claude
continua implementando direto (edita arquivos, roda testes). Guia completo:
[plugin/README.md](plugin/README.md).

**Pelo diretório de plugins do Claude** (assim que o Jevin for aprovado; o envio está em
análise):

1. No claude.ai ou no app desktop, abra **Personalização › Plugins**
   ([claude.ai/customize/plugins](https://claude.ai/customize/plugins); em inglês, *Customize ›
   Plugins*), vá na aba **Discover**, escolha **Jevin** e clique em **Add**.
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
o `jev-claud setup` reconhece o `jevin@synced` e remove a cópia duplicada. Sem o diretório,
o `setup` instala pelo marketplace.

O plugin se chama **Jevin**, como o mascote. Até a 0.6 o projeto se chamava
`jev-claude`: quem vem de lá roda o instalador de novo e depois `jev-claud setup`, que troca
marketplace, plugin e MCP antigos pelos novos (o comando `jev-claude` continua funcionando como
atalho).

| Peça | O que faz | Custo |
| --- | --- | --- |
| Skill `jev` | Ensina quando usar Jev, LLM ou código puro, quando as ferramentas MCP compensam e como ler as saídas compactadas. Só entra no contexto quando é relevante | — |
| Subagente `scout` | Explora o código em contexto separado (modelo menor) e devolve só a conclusão com `caminho:linha` | barato |

Funciona no Claude Code e no Cowork; nas conversas do claude.ai, só a skill. Até a 0.7 o hook de
compactação vinha no plugin; desde a 0.8 ele é instalado pelo `jev-claud setup` (rode-o de novo
depois de atualizar).

## Também no Cursor e no Codex

O servidor MCP do jev-claud funciona em qualquer agente compatível com MCP. Quando o
`jev-claud setup` encontra o Cursor ou o Codex neste computador, ele oferece configurá-los
também, perguntando antes de cada passo, como faz com o Claude Code:

| Passo | Cursor | Codex |
| --- | --- | --- |
| Ferramentas MCP (`classify`, `rank`, `summarize_file`, `decide`, `write`) | `~/.cursor/mcp.json` | tabela `[mcp_servers.jev-claud]` no `~/.codex/config.toml` |
| Regra "Jev ramifica, LLM escreve" | `jev-claud rule` mostra o texto; cole em *Cursor Settings › Rules* (o Cursor não tem arquivo de regras globais) | `~/.codex/AGENTS.md` (ou o `AGENTS.override.md`, se você usa um) |

- No Cursor, reinicie depois do setup e confira em *Settings › Tools & MCP* se o jev-claud está
  ligado.
- O Codex usa `$CODEX_HOME` no lugar de `~/.codex`, se a variável estiver definida.
- O setup preserva o resto de cada arquivo e troca só a entrada do jev-claud (por exemplo, a de
  outro ambiente virtual). Um arquivo que ele não consiga ler com segurança fica como está.
- A compactação das saídas de teste e o plugin Jevin, por enquanto, são só do Claude Code.

## Configuração (tudo opcional)

O `jev-claud setup` grava `config.json` em `JEV_CLAUD_HOME`. Variáveis de ambiente sempre
têm prioridade sobre o arquivo.

| Variável | Padrão | Efeito |
| --- | --- | --- |
| `TYPESAFE_API_KEY` | — | Chave do Jev ([console](https://console.typesafe.ai/keys)). |
| `JEV_CLAUD_BUDGET_USD` | `0` | Teto mensal para o Jev. `0` = custo zero (offline). Ex.: `1.00` ≈ 23 milhões de tokens de entrada. |
| `JEV_CLAUD_MODEL` | `latest` | `latest` (estável, atualizado pela TypeSafe), `preview` (mais novo), `newest` (descobre e fixa a versão mais nova por 24 h) ou `jev-X.Y.Z` (fixo). |
| `JEV_CLAUD_USD_PER_MTOK` | `0.042` | Preço por milhão de tokens de entrada, caso mude. |
| `JEV_CLAUD_STRATEGY` | `cascade` | `cascade` (offline primeiro, Jev só nos incertos) ou `jev` (tudo no Jev). |
| `JEV_CLAUD_CASCADE_ACCEPT` | `0.8` | Confiança mínima para aceitar a resposta offline. Suba para escalar mais ao Jev. |
| `JEV_CLAUD_CACHE_TTL_DAYS` | `7` | Validade do cache para aliases. Versões fixas não expiram. `0` desliga. |
| `JEV_CLAUD_HOME` | cache do SO | Onde fica o SQLite (cache + gastos). |
| `JEV_CLAUD_LLM_BASE_URL` / `JEV_CLAUD_LLM_MODEL` / `JEV_CLAUD_LLM_API_KEY` | — | LLM de texto em qualquer endpoint compatível com OpenAI. |
| `JEV_CLAUD_OLLAMA_MODEL` / `OLLAMA_HOST` | `llama3.2` / `localhost:11434` | Ollama local, detectado automaticamente. |

## API

### Decisões (o código consome)

```python
d = r.decide(state, {"nome": pergunta, ...})   # -> Decisions (Mapping[str, Decision])
r.ask(state, pergunta)                          # -> Decision (uma pergunta só)
```

`state` pode ser texto, dict ou lista. Em dicts, referencie campos nas instruções com
crases: ``"O texto em `ticket.body` pede reembolso?"``.

| Pergunta | Construtor | `Decision.value` | Atalhos |
| --- | --- | --- | --- |
| Uma opção de um conjunto | `choice(instr, {"rótulo": "descrição"})` ou `choice(instr, ["a", "b"])` | rótulo (`str`) | `.is_("a")`, `.probability` |
| Condição sim/não | `noul(instr, yes=..., no=...)` | `bool` (p > 0,5) | `.yes`, `.probability` = p(sim) |
| Grau em níveis ordenados | `score(instr, ["baixo", "médio", "alto"])` | `float` esperado | `.at_least(1.5)`, `.level` |

Toda `Decision` traz `confidence`, `band` (`HIGH`/`MEDIUM`/`LOW`), `probabilities`,
`source` (`jev`/`cache`/`offline`) e `model`. A `Policy(act=0.75, confirm=0.5)` define as
faixas; ajuste ao custo de errar (ação destrutiva pede limiar maior que leitura).

### Ramificação direta

```python
# if semântico
r.when(
    msg,
    "O cliente quer cancelar?",
    then=fluxo_cancelamento,
    otherwise=fluxo_normal,
    uncertain=perguntar_ao_cliente,
)

# match semântico: executa a função do ramo escolhido (recebe a Decision se aceitar argumento)
r.switch(
    msg,
    "Qual a intenção do cliente?",
    {
        "status_pedido": Case("onde está, rastreio, entrega", then=consultar_pedido),
        "reclamacao": Case("problema, defeito, insatisfeito", then=abrir_reclamacao),
        "duvida": Case("como funciona, pergunta sobre produto", then=responder_duvida),
    },
    default=enviar_para_humano,
)

# ranking por relevância (um Score comparável por item, numa requisição só)
for hit in r.rank("como cancelar a assinatura", documentos, top_k=3):
    print(hit.score, hit.item)
```

### Cascata: pague só pelo difícil

Com a chave e o orçamento do Jev configurados, `decide` (e `when`, `switch`, `rank`) segue:

```
1. cache local        mesma pergunta + mesmo estado + mesmo modelo  -> grátis
2. offline            confiança >= JEV_CLAUD_CASCADE_ACCEPT (0,8)  -> grátis
3. Jev (System One)   só as perguntas incertas, numa requisição     -> pago
4. sua Policy         confiança baixa até no Jev -> Band.LOW -> humano/padrão
```

```python
d = r.decide(ticket, {"setor": setor, "urgente": urgente})
d.source  # "offline", "jev", "cache" ou "mixed" (parte offline, parte Jev)
d.escalated  # ("urgente",): o que foi ao Jev
d["setor"].source  # de onde veio cada resposta
```

- O modo offline é conservador: só fica confiante quando o texto tem os termos das opções.
  Descreva cada opção com as palavras que aparecem nos dados reais e mais coisas saem grátis.
- `rank` nunca mistura notas offline e do Jev (não são comparáveis): ou tudo offline, ou tudo no Jev.
- Imagens e `box` vão sempre ao Jev (o offline não enxerga imagens).
- `JEV_CLAUD_STRATEGY=jev` (ou `JevClaud(strategy="jev")`) manda tudo ao Jev, como antes.
- `jev-claud spend` mostra quantas perguntas cada camada respondeu e a economia estimada.

### Imagens (System One com visão)

```python
from jev_claud import Image, box, noul

foto = Image("devolucao_123.jpg")  # caminho ou bytes (PNG/JPEG)
d = r.decide(
    {"pedido": "#123", "foto": foto},  # a imagem pode ir no estado, em listas...
    {
        "danificado": noul("O produto chegou danificado?"),
        "onde": box("Onde está o dano?"),  # Box: localiza algo na imagem
    },
)
if d["danificado"].yes and d["danificado"].confident:
    x0, y0, x1, y1 = d["onde"].region.to_pixels(largura, altura)  # pronto para o Pillow

r.when(f"Comparando {foto} com {Image('original.jpg')}", "A peça foi trocada?")  # ...ou no texto
```

Exigem o Jev com um modelo de visão (`JEV_CLAUD_MODEL`). A imagem é enviada como anexo; o
custo estimado é ~1.500 tokens por imagem. Pela CLI:
`jev-claud ask --image foto.jpg --box "Onde está o dano?"`.

### Texto (uma pessoa lê)

```python
r.write("Resuma o ticket para o time em 3 linhas.", context=ticket, system="Seja objetivo.")
```

LLMs suportadas: `OllamaWriter` (local, grátis), `OpenAICompatibleWriter(base_url, model,
api_key=...)` e `CallableWriter(fn)` para plugar qualquer SDK:

```python
from jev_claud import CallableWriter, JevClaud

r = JevClaud(writer=CallableWriter(lambda prompt, system: minha_llm(prompt, system)))
```

### Async

`adecide`, `aask` e `awrite` têm a mesma semântica sem bloquear o event loop.

## CLI

```bash
jev-claud setup                        # assistente: instala e configura tudo
jev-claud mcp                          # servidor MCP (stdio) usado pelo Claude Code, Cursor e Codex
jev-claud rule                         # a regra "Jev ramifica, LLM escreve" para colar em outro agente
jev-claud run -- pytest -x            # roda teste/build e compacta a saída (sem LLM)
npm test 2>&1 | jev-claud compact     # o mesmo, como filtro de texto
jev-claud doctor                       # backends ativos, modelo, orçamento, cache
jev-claud ask "Fui cobrado duas vezes" --choice "Qual setor?" -o financeiro=cobrança -o suporte=erro
jev-claud ask "Preciso disso hoje" --noul "É urgente?"
jev-claud write "Explique o plano Pro em 2 frases"
jev-claud models                       # modelos Jev disponíveis na conta
jev-claud spend                        # gastos registrados por mês
jev-claud update                       # atualiza jev-claud e typesafe-sdk, redescobre o modelo mais novo
jev-claud cache clear
```

## Como desenhar boas perguntas

Baseado no [guia oficial](https://docs.typesafe.ai/concepts/how-to-build-with-system-one):

1. **Uma decisão estreita por pergunta.** Separe dimensões independentes (setor, urgência,
   tom) em perguntas diferentes, na mesma chamada.
2. **Dê estado suficiente.** Texto-fonte, identidades, políticas e fatos atuais, em campos
   JSON nomeados quando houver várias partes.
3. **Descreva as opções.** Descrições com os termos reais ajudam o Jev e o modo offline.
4. **Inclua um "nenhum"** quando nada puder servir.
5. **Política explícita em código.** Use `confidence`/`band` com limiares testados nos seus
   dados; saída tipada garante a interface, não a verdade.

## Desenvolvimento

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
ruff check . && mypy
```

Os testes não fazem chamadas de rede nem gastam nada: o Jev é simulado com os tipos reais
do `typesafe-sdk`, e a LLM de texto com um servidor HTTP local.

## Suporte

Problemas comuns e como pedir ajuda: [SUPPORT.md](SUPPORT.md). Vulnerabilidades:
[SECURITY.md](SECURITY.md). Privacidade: [PRIVACY.md](PRIVACY.md).

## Licença

[MIT](LICENSE). TypeSafe e Jev são marcas da TypeSafe AI; este projeto é independente.
