# AIR — AI Runtime

Camada de infraestrutura de execução para agentes/LLM. **Não é mais um
agent framework.** O princípio central:

> Informação persistente e estruturada NÃO deve ser enviada repetidamente
> como tokens ao LLM.

Isso é operacionalizado por duas primitivas de execução, deliberadamente
separadas (não é "tudo memória pra RAG"):

- **World State** (`world/state.py`) — entidade/relação/evento
  consultável fora do prompt: `world.dependents_of("api")` responde "o
  que depende da API" por consulta direta, sem reconstruir nada de
  histórico de conversa.
- **Memory** (`memory/store.py`) — fatos discretos com recência explícita
  (`remember`/`recall`), mesmo padrão validado no experimento
  `../struct-reasoning/memory`.
- **Context Engine** (`context/engine.py`) — a peça que faz o princípio
  central valer de verdade: outputs grandes de tool viram um *handle*
  curto (`put`/`get`); só entram inteiros no prompt se pequenos ou
  `pinned`, senão viram referência + resumo (`render`).

## Por que estas peças e não outras

Antes de escrever qualquer código, este projeto fez uma pesquisa real de
ecossistema (`docs/ECOSYSTEM_RESEARCH.md`). Conclusão: a maior parte do
que um "agent runtime" precisa (protocolo de tool = MCP, browser =
Playwright, sandbox = Firecracker/gVisor, model provider = LiteLLM,
durabilidade = Temporal, vector DB = Qdrant/LanceDB, grafo de memória =
Graphiti, extração de fato = Mem0, tracing = Langfuse) **já é maduro** —
reimplementar seria desperdício. As peças que este repo constrói de
verdade são só as que a pesquisa confirmou como lacuna real: Context
Engine, World State, Verification Engine (`verification/engine.py`),
permissão granular por capacidade (`security/permissions.py`) e Planner
(`planner/planner.py`). O resto (`filesystem/`, `process/`) é
propositalmente uma casca fina.

## Estrutura

```
core/          tipos compartilhados (Entity, Relation, Event, Fact, Task, Goal, ActionResult, Verification, Capability)
world/         World State (SQLite)
memory/        fatos discretos com recência (SQLite)
context/       Context Engine — referência por ID
events/        EventBus efêmero (pub/sub em processo, diferente do log durável de world.event)
security/      permissão por capacidade, allowlist por padrão
verification/  decide sucesso semântico de ação, não só ausência de erro
planner/       decomposição de goal em tasks com dependência + verificação por passo
tools/         registro de tool, aplica permissão e roteia output grande pro Context Engine
models/        abstração de model provider (LiteLLM adapter, provider local via transformers, EchoProvider p/ teste)
filesystem/    tools de arquivo (casca fina)
process/       tool de comando (casca fina)
sdk/           fachada Agent(), junta tudo
mcp_server/    servidor MCP (Claude Code fala com o AIR por aqui — ver secão dedicada abaixo)
tests/         suites sem dependência externa (`python tests/test_air.py`, `python tests/test_mcp_server.py`)
benchmarks/    comparação de tokens: agente de histórico completo vs agente sobre o AIR
examples/      demos reais (`demo_agent.py` = agente completo; `demo_mcp.py` = fluxo MCP store→nova sessão→query)
```

## Rodar

```
cd air
python tests/test_air.py
python tests/test_mcp_server.py
python benchmarks/token_benchmark.py
python examples/demo_agent.py
python examples/demo_mcp.py
```

## MCP Server — o AIR como memória externa do Claude Code

**AIR não substitui o contexto interno do Claude.** O `mcp_server/`
expõe o AIR como uma camada *externa* de memória/context-retrieval que o
Claude Code pode consultar via [MCP](https://modelcontextprotocol.io)
durante uma sessão — pra' recuperar informação persistida, armazenar
fatos novos, e reconstruir contexto mínimo sob demanda, sem precisar que
tudo seja re-explicado em prosa a cada sessão nova.

```
Claude Code → MCP (stdio) → mcp_server/server.py → mcp_server/adapter.py → World State / Memory / Context Engine / Planner
```

`mcp_server/adapter.py` é a única ponte: ele não conhece nada de MCP, só
recebe `str`/`int`/`dict` e devolve `dict`. `server.py` é a única parte
que conhece o protocolo (SDK oficial `mcp`, classe `MCPServer`,
transporte stdio). O núcleo do AIR (`world/`, `memory/`, `context/`,
`planner/`, `verification/`) continua 100% utilizável sem MCP — nenhuma
API pública dessas pastas mudou, só ganharam métodos de consulta novos
(`all_active`, `all_entities`, `all_events`, `get_fact`) que qualquer
consumidor pode usar.

Distinção importante com a pesquisa original (`docs/ECOSYSTEM_RESEARCH.md`):
lá, "adotar MCP" significava o AIR *consumir* tools externas via MCP
(ainda não implementado — `tools/registry.py` continua agnóstico de
protocolo). Aqui é o oposto: o AIR *é* um servidor MCP, consumido pelo
Claude Code. As duas direções são independentes.

### Tools expostas

| Tool | O que faz | Por baixo |
|---|---|---|
| `air_search_context(query, limit=5)` | Busca por palavra-chave (não semântica — ver `method` no retorno) em Memory + World State | `mcp_server/retrieval.py` |
| `air_store_memory(content, metadata={subject,predicate,reason})` | Grava um fato; se `subject+predicate` já existir, a versão nova *supersede* a antiga (recência, não sobrescrita) | `MemoryStore.remember` |
| `air_get_context(query, max_tokens=2000)` | Fluxo completo: Planner → retrieval → resolução de recência/conflito → montagem final respeitando orçamento de tokens | `Planner` + `ContextEngine` |
| `air_update_memory(id, content)` | Cria nova versão que supersede a antiga (só em fatos `ACTIVE`) | `MemoryStore.remember` |
| `air_delete_memory(id)` | Soft-delete (marca `DELETED`, não apaga a linha) | `MemoryStore.forget` |

Resources: `air://memory/facts` (snapshot dos fatos ativos), `air://world/state`
(entidades + eventos recentes). Prompt: `reconstruct_context` (orienta
uso de `air_get_context` e como recência já resolve conflito).

### Configurar no Claude Code

Arquivo `.mcp.json` na raiz do projeto (já criado neste repo):

```json
{
  "mcpServers": {
    "air": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "E:\\x\\air",
      "env": {
        "AIR_STORAGE": "E:\\x\\air\\storage\\air_mcp.db",
        "AIR_LOG_LEVEL": "INFO",
        "AIR_MAX_CONTEXT": "2000",
        "AIR_ENABLE_STRUCTURAL_MEMORY": "true"
      }
    }
  }
}
```

Depois de criar/editar `.mcp.json`, é preciso **reiniciar o Claude
Code** (ou a sessão atual) pra' ele carregar o servidor — registrar o
arquivo não conecta retroativamente numa sessão já em execução.

Verificar que está funcionando: dentro do Claude Code, `/mcp` deve listar
`air` como conectado, com as 5 tools acima disponíveis. Sem o Claude
Code, dá pra' testar o servidor isolado com `python -m mcp_server.server`
(fica esperando entrada stdio — Ctrl+C pra' sair) ou rodar
`python examples/demo_mcp.py`, que chama a mesma camada de protocolo sem
precisar de um client MCP de verdade do outro lado.

### Configuração (variáveis de ambiente)

| Variável | Default | Efeito |
|---|---|---|
| `AIR_STORAGE` | `<raiz do projeto>/storage/air_mcp.db` | Arquivo SQLite compartilhado por World State + Memory (tabelas não colidem, um arquivo só) |
| `AIR_LOG_LEVEL` | `INFO` | Nível de log (vai pra' stderr — stdout é reservado pro protocolo JSON-RPC) |
| `AIR_MAX_CONTEXT` | `2000` | Orçamento de tokens default pra' `air_get_context` quando `max_tokens` não é passado |
| `AIR_ENABLE_STRUCTURAL_MEMORY` | `true` | `true` = fatos renderizados como `FACT(subject,predicate,valor)` (mesma notação validada em `struct-reasoning/memory`, N=200, +12pp acurácia / −31.6% tokens vs prosa); `false` = prosa (`"X tem Y igual a Z"`) |
| `AIR_MAX_CONTENT_CHARS` | `20000` | Limite de tamanho de `content` em `air_store_memory`/`air_update_memory` |
| `AIR_MAX_QUERY_CHARS` | `1000` | Limite de tamanho de `query` |
| `AIR_DEFAULT_SEARCH_LIMIT` | `5` | Resultados default de `air_search_context` |

Nenhuma chave de API é lida ou usada por este servidor — ele só fala com
o AIR local (SQLite), não com nenhum provider de LLM.

### Honestidade metodológica

- **Busca por palavra-chave, não embeddings.** `retrieval.py` pontua por
  overlap de palavras (substring, case-insensitive) entre a query e o
  texto serializado de cada fato/entidade/evento. Todo resultado inclui
  `"method": "keyword_substring_overlap"` — não finge ser busca semântica.
- **Token accounting real quando possível.** `mcp_server/tokens.py` usa o
  tokenizador do SmolLM2-360M-Instruct (mesmo modelo usado nos
  experimentos desta sessão) via `local_files_only=True` — carrega só do
  cache local (medido: checar a Hugging Face Hub por atualização, mesmo
  com cache, levou ~130s numa chamada, inviável por tool call; local-only
  fica em segundos). Se o cache não existir, cai pra' heurística
  `len(texto)//4` — e o campo `method` do retorno sempre diz qual dos
  dois foi usado, nunca afirma token real sem ser.
- **Recência/conflito resolvidos na origem, não escondidos.** `air_get_context`
  não faz nada "mágico" pra' resolver conflito — `MemoryStore.remember`
  já garante que só existe um fato `ACTIVE` por `subject+predicate` (o
  antigo vira `SUPERSEDED`, nunca é apagado). A busca e a reconstrução de
  contexto só leem fatos `ACTIVE`, então a versão antiga nunca vaza —
  testado explicitamente em `tests/test_mcp_server.py::test_conflicting_context_resolves_to_latest`.
- **Concorrência**: o SDK MCP despacha cada tool call síncrona num worker
  thread (`anyio.to_thread.run_sync`). Isso quebrava o SQLite (conexões
  são thread-affine por padrão) até ser corrigido com
  `check_same_thread=False` em `world/state.py`/`memory/store.py` + uma
  trava (`threading.Lock`) em `mcp_server/adapter.py` — bug real
  encontrado durante o desenvolvimento, não hipotético, com teste de
  regressão cobrindo a chamada via protocolo MCP de verdade.

### Limitações conhecidas do MCP server

- Busca é palavra-chave, não semântica — uma pergunta parafraseada sem
  nenhuma palavra em comum com o fato armazenado não vai encontrá-lo.
- `air_store_memory` só grava em Memory (fatos `subject/predicate/obj`).
  World State (entidade/relação/evento) é consultável via
  `air_search_context`/`air_get_context`, mas não é gravável por nenhuma
  tool MCP ainda — não havia um mapeamento natural de "content" livre pra'
  entidade/relação estruturada sem inventar um formato, então essa
  gravação ficou de fora desta primeira versão.
- `max_tokens` em `air_get_context` não impede que um único fato maior
  que o orçamento entre sozinho no contexto (a alternativa seria devolver
  contexto vazio mesmo tendo achado algo, o que é pior).
- **Cold-start do tokenizer pode ser MUITO mais lento que o esperado.**
  `mcp_server/tokens.py` carrega `AutoTokenizer.from_pretrained(...,
  local_files_only=True)` já com essa flag pra nunca bater na rede — mas
  mesmo assim, medido nesta sessão: a primeira chamada real que precisou
  do tokenizer levou **208 segundos**; a segunda chamada idêntica levou
  **2.9ms** (fica em cache no processo depois do primeiro load). Root
  cause provável: I/O pathológico de disco quase cheio (não confirmado
  como universal, só medido nesta máquina) — mas o efeito é real e sério
  o bastante pra parecer travado: um cliente MCP com timeout menor que
  ~210s vai ver isso como falha, não como lentidão. Ainda não tem
  fallback/timeout no código pra isso — é uma lacuna real, não só
  hipotética.

## Benchmark de token — o que ele mostra de verdade

`benchmarks/token_benchmark.py` mede, com tokenizador real
(SmolLM2-360M-Instruct, mesmo modelo usado nos experimentos
`struct-reasoning` desta sessão), quantos tokens um agente gasta numa
sessão de N turnos investigando uma queda de serviço, comparando:

- **Tradicional**: cada turno reenvia o histórico inteiro (pergunta +
  output de tool completo de todo turno anterior) — como a maioria dos
  frameworks de agente hoje monta o prompt.
- **AIR**: fato estrutural fica no World State (consultado, não
  reenviado em prosa); só os handles dos últimos 2 outputs de tool
  entram no Context Engine render — os mais antigos existem como evento
  consultável, não como texto bruto reempilhado.

Resultado medido (`results/token_benchmark.json`): 87.7% de economia em
5 turnos, 96.8% em 15, 98.5% em 30 — crescente porque o custo
tradicional cresce ~quadrático com o histórico enquanto o AIR fica
praticamente plano por turno.

**Ressalva honesta**, na mesma linha do que já foi feito nos
experimentos `struct-reasoning`/LongMemEval desta sessão: este é um
cenário sintético desenhado para *demonstrar o mecanismo funcionando*,
não uma medição em log de produção real. O tamanho da economia depende
diretamente de quanto do histórico realmente vira "estado consultável"
em vez de "texto que precisa ser relido" — em uma tarefa onde cada turno
depende genuinamente do texto completo dos turnos anteriores (não só do
estado resumível), a economia seria menor. O valor real do número aqui é
mostrar que o mecanismo de referência-por-ID funciona e tem direção
correta, não que 98.5% é o que qualquer agente real vai obter.

## O que ainda falta

`adapters/` ainda está vazio: o AIR expõe-se como servidor MCP
(`mcp_server/`), mas ainda não *consome* nada maduro via adapter próprio
— Firecracker/E2B (sandbox), Temporal (durabilidade) e Graphiti (grafo de
memória) continuam na lista de "adotar, não construir" da pesquisa
original, não implementados. O SDK JS/TS também não existe ainda (pedido
explícito do usuário: Python primeiro).
