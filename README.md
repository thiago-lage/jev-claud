# jev-claude

**Decisões tipadas para o código. Texto livre para pessoas. Custo zero por padrão.**

`jev-claude` separa as duas saídas de IA que um app tem:

| Quem consome a resposta? | Use | Motor | Por quê |
| --- | --- | --- | --- |
| **O código**, para ramificar (`if`, `match`, roteamento, filtro, ranking) | `decide`, `when`, `switch`, `rank` | [TypeSafe **Jev**](https://docs.typesafe.ai) (System One) | Resposta tipada e calibrada (rótulo, probabilidade, grau). Sem gerar JSON, sem parse, sem tokens de saída pagos. |
| **Uma pessoa**, que vai ler | `write` | LLM (Ollama local, endpoint compatível com OpenAI, ou qualquer função) | Texto natural é o que LLMs fazem bem. |

Regras conhecidas, cálculos e buscas exatas continuam em código puro: a IA entra só onde
falta senso comum semântico.

```python
from jev_claude import JevClaude, Case, choice, noul, score

r = JevClaude()

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
irm https://raw.githubusercontent.com/thiago-lage/jev-claude/main/install.ps1 | iex
```

**macOS / Linux**:

```bash
curl -fsSL https://raw.githubusercontent.com/thiago-lage/jev-claude/main/install.sh | bash
```

O assistente `jev-claude setup` funciona como o primeiro `claude` no terminal: mostra o que
falta, explica para que serve e **pergunta antes de instalar cada coisa**:

```
╭──────────────────────────────────────────────╮
│ ✻ Bem-vindo ao jev-claude setup!             │
│                                              │
│   Jev decide o que o código ramifica.        │
│   LLM escreve o que a pessoa lê.             │
│   Custo zero por padrão.                     │
╰──────────────────────────────────────────────╯

 ✔ Python 3.10+  3.12.10 (ambiente isolado)
 ✔ SDK do Jev (typesafe-sdk)  instalado
 ✖ Ollama (LLM local e grátis para texto)  texto grátis precisa de uma LLM local
   › Instalar o Ollama via winget? (aceita os termos do pacote; licença MIT) (S/n)
```

| Passo | O que faz |
| --- | --- |
| Ollama + servidor + modelo | Instala (winget / Homebrew / script oficial), inicia e baixa o `llama3.2` (~2 GB): texto grátis no `write()`. |
| Chave do Jev | Opcional. Guarda a chave e o orçamento mensal em `config.json` (padrão US$ 0). |
| Claude Code | Instalador oficial de claude.ai. |
| Skill TypeSafe | `claude plugin install typesafe@typesafe-ai`. |
| Servidor MCP | Registra o `jev-claude` no Claude Code para delegar lotes e economizar tokens. |
| Regra global | Ensina o Claude, no `~/.claude/CLAUDE.md`, quando usar Jev e quando usar LLM. |

```bash
jev-claude setup            # interativo (também abre sozinho no primeiro `jev-claude`)
jev-claude setup --yes      # aceita tudo que não exige digitar nada
jev-claude setup --check    # só verifica; sai com 1 se faltar algo essencial
jev-claude setup --model qwen2.5:3b   # outro modelo do Ollama
```

### Instalação manual

```bash
pip install "jev-claude[jev] @ git+https://github.com/thiago-lage/jev-claude"
```

Sem o extra `[jev]` o pacote não tem nenhuma dependência e roda só no modo offline. Prefira
um ambiente virtual: o `typesafe-sdk` exige versões recentes de `anyio`/`h11`, que podem
conflitar com pacotes antigos no Python global.

Exemplo completo, que roda sem configurar nada: [`examples/triagem_suporte.py`](examples/triagem_suporte.py).

## Dentro do Claude: servidor MCP para economizar tokens

O `jev-claude setup` registra o jev-claude como servidor MCP do Claude Code (ou faça à mão):

```bash
claude mcp add -s user jev-claude -- python -m jev_claude mcp
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

## Configuração (tudo opcional)

O `jev-claude setup` grava `config.json` em `JEV_CLAUDE_HOME`. Variáveis de ambiente sempre
têm prioridade sobre o arquivo.

| Variável | Padrão | Efeito |
| --- | --- | --- |
| `TYPESAFE_API_KEY` | — | Chave do Jev ([console](https://console.typesafe.ai/keys)). |
| `JEV_CLAUDE_BUDGET_USD` | `0` | Teto mensal para o Jev. `0` = custo zero (offline). Ex.: `1.00` ≈ 23 milhões de tokens de entrada. |
| `JEV_CLAUDE_MODEL` | `latest` | `latest` (estável, atualizado pela TypeSafe), `preview` (mais novo), `newest` (descobre e fixa a versão mais nova por 24 h) ou `jev-X.Y.Z` (fixo). |
| `JEV_CLAUDE_USD_PER_MTOK` | `0.042` | Preço por milhão de tokens de entrada, caso mude. |
| `JEV_CLAUDE_CACHE_TTL_DAYS` | `7` | Validade do cache para aliases. Versões fixas não expiram. `0` desliga. |
| `JEV_CLAUDE_HOME` | cache do SO | Onde fica o SQLite (cache + gastos). |
| `JEV_CLAUDE_LLM_BASE_URL` / `JEV_CLAUDE_LLM_MODEL` / `JEV_CLAUDE_LLM_API_KEY` | — | LLM de texto em qualquer endpoint compatível com OpenAI. |
| `JEV_CLAUDE_OLLAMA_MODEL` / `OLLAMA_HOST` | `llama3.2` / `localhost:11434` | Ollama local, detectado automaticamente. |

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

### Texto (uma pessoa lê)

```python
r.write("Resuma o ticket para o time em 3 linhas.", context=ticket, system="Seja objetivo.")
```

LLMs suportadas: `OllamaWriter` (local, grátis), `OpenAICompatibleWriter(base_url, model,
api_key=...)` e `CallableWriter(fn)` para plugar qualquer SDK:

```python
from jev_claude import CallableWriter, JevClaude

r = JevClaude(writer=CallableWriter(lambda prompt, system: minha_llm(prompt, system)))
```

### Async

`adecide`, `aask` e `awrite` têm a mesma semântica sem bloquear o event loop.

## CLI

```bash
jev-claude setup                        # assistente: instala e configura tudo
jev-claude mcp                          # servidor MCP (stdio) usado pelo Claude Code
jev-claude doctor                       # backends ativos, modelo, orçamento, cache
jev-claude ask "Fui cobrado duas vezes" --choice "Qual setor?" -o financeiro=cobrança -o suporte=erro
jev-claude ask "Preciso disso hoje" --noul "É urgente?"
jev-claude write "Explique o plano Pro em 2 frases"
jev-claude models                       # modelos Jev disponíveis na conta
jev-claude spend                        # gastos registrados por mês
jev-claude update                       # atualiza jev-claude e typesafe-sdk, redescobre o modelo mais novo
jev-claude cache clear
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

## Licença

[MIT](LICENSE). TypeSafe e Jev são marcas da TypeSafe AI; este projeto é independente.
