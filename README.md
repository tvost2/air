# AIR — AI Runtime

[![tests](https://github.com/tvost2/air/actions/workflows/tests.yml/badge.svg)](https://github.com/tvost2/air/actions/workflows/tests.yml)

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
adapters/      adota tech madura em vez de reimplementar (semantic_search.py — opcional, desligado por padrão)
tests/         suites (`test_air.py`/`test_mcp_server.py`/`test_kakeya_index.py`/`test_tokens.py` só dependem de requirements.txt; `test_semantic_search.py` precisa de requirements-optional.txt + AIR_ENABLE_SEMANTIC_SEARCH=true)
benchmarks/    token_benchmark.py (sessão N turnos) + context_comparison/ (AIR vs. 9 outras abordagens, 96 casos — ver seção dedicada abaixo)
examples/      demos reais (`demo_agent.py` = agente completo; `demo_mcp.py` = fluxo MCP store→nova sessão→query)
```

`requirements.txt` (mínimo pro servidor MCP) e `requirements-optional.txt`
(cada bloco liga uma feature opcional específica — tokenizador real,
busca semântica, provider LiteLLM) documentam as dependências reais do
projeto, versão testada nesta máquina (não suposta) — ver comentários em
cada arquivo.

## Rodar

```
cd air
pip install -r requirements.txt   # minimo pro servidor MCP + tests/test_air.py,
                                    # test_mcp_server.py, test_kakeya_index.py, test_tokens.py

python tests/test_air.py
python tests/test_mcp_server.py
python tests/test_kakeya_index.py
python tests/test_tokens.py
python examples/demo_mcp.py

# tudo abaixo precisa de requirements-optional.txt (ver esse arquivo pra'
# instalar so' o bloco que voce for usar -- cada um liga uma feature
# diferente, nenhum e' necessario pro servidor MCP em si):
pip install -r requirements-optional.txt

python benchmarks/token_benchmark.py   # precisa de transformers/torch
python examples/demo_agent.py           # idem (HFLocalProvider)

# opcional e lento (~187s de import na 1a chamada, ver "Busca semântica
# opcional" acima) -- so' roda de verdade com a env var ligada:
AIR_ENABLE_SEMANTIC_SEARCH=true python tests/test_semantic_search.py
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
| `air_search_context(query, limit=5, project="")` | Busca por palavra-chave (mais busca semântica opcional — ver seção "Busca semântica opcional" abaixo e `method` no retorno) em Memory + World State | `mcp_server/retrieval.py` |
| `air_store_memory(content, metadata={subject,predicate,reason,project})` | Grava um fato; se `subject+predicate` já existir NO MESMO `project`, a versão nova *supersede* a antiga (recência, não sobrescrita) | `MemoryStore.remember` |
| `air_register_entity(kind, name, attrs={}, project="")` | Registra no World State um artefato já construído (API, frontend, módulo) pra uma tarefa futura achar e *readaptar* em vez de refazer do zero. Idempotente por `name` exato (registrar de novo com mesmo nome não atualiza, só devolve o existente — use `air_update_entity` pra isso). Retorno inclui o custo real em tokens de reusar (`_context_cost_tokens`/`_context_cost_method` nos `attrs`) e `possible_duplicates` (nomes parecidos já registrados — aviso, não bloqueia nem faz merge automático) | `WorldState.entity` |
| `air_update_entity(id, attrs=None, kind=None, merge_attrs=True)` | Atualiza `kind`/`attrs` de uma entidade existente sem precisar apagar e registrar de novo. `merge_attrs=True` (default) combina com os attrs existentes; `False` substitui inteiro. Não atualiza `name`/`project` de propósito | `WorldState.update_entity` |
| `air_register_relation(source_id, kind, target_id, project="")` | Registra uma relação entre duas entidades já existentes (ex: `depends_on`) — é o que `world.dependents_of()` consulta. Erro claro se `source_id`/`target_id` não existirem | `WorldState.relation` |
| `air_delete_entity(id)` | Remove uma entidade por id (hard delete — `Entity` não tem campo de status pra soft-delete como `Fact`). Serve principalmente pra corrigir duplicata de registro | `WorldState.delete_entity` |
| `air_get_context(query, max_tokens=2000, project="")` | Fluxo completo: Planner → retrieval → resolução de recência/conflito → montagem final respeitando orçamento de tokens | `Planner` + `ContextEngine` |
| `air_update_memory(id, content)` | Cria nova versão que supersede a antiga (só em fatos `ACTIVE`) | `MemoryStore.remember` |
| `air_delete_memory(id)` | Soft-delete (marca `DELETED`, não apaga a linha) | `MemoryStore.forget` |

### Anotações MCP e `instructions` — o cliente sabe o que cada tool faz sem adivinhar

Cada uma das 9 tools declara `title` e `ToolAnnotations`
(`read_only_hint`/`destructive_hint`/`idempotent_hint`/`open_world_hint`)
— campos padrão do protocolo MCP que existiam na SDK instalada mas não
estavam sendo usados. Não é decoração: um cliente MCP pode usar isso pra
decidir se pede confirmação antes de uma tool destrutiva, ou se é seguro
tentar de novo depois de uma falha de rede. Classificação verificada
contra o comportamento real de cada tool em `mcp_server/adapter.py`, não
suposta — por exemplo:

- `air_register_entity` é `idempotent_hint=True` porque de fato dedupa
  por `name` (chamar duas vezes converge pro mesmo estado), mas
  `air_register_relation` é `idempotent_hint=False` porque **não** faz
  esse dedup — chamar duas vezes cria duas relações separadas (achado
  ao verificar o código, não assumido por analogia com a tool anterior).
- `air_delete_memory` é `destructive_hint=True` mesmo sendo *soft*-delete
  no storage (a linha continua no SQLite, só marcada `DELETED`) —
  porque não existe tool de desfazer, o efeito é irreversível do ponto
  de vista de quem chama, que é o que a anotação realmente comunica.

O servidor também declara `instructions` (campo MCP separado de
`description`, pensado especificamente pra orientar o LLM sobre como
usar o servidor como um todo) — sintetiza num só lugar o que antes só
vivia espalhado nas docstrings de cada tool: buscar antes de reconstruir,
registrar entidade ao terminar de construir algo reutilizável, sempre
passar `project=` quando a sessão tiver um projeto identificável, e que
tools destrutivas não têm desfazer.

### Isolamento entre projetos/sessões (`project`)

Por padrão, o `AIR_STORAGE` é um único arquivo SQLite compartilhado por
**qualquer** sessão Claude Code que conecte no MCP `air` (registro global
em `~/.claude.json`, não por projeto) — descoberta real feita rodando duas
sessões em paralelo: elas liam/escreviam no mesmo `world`/`memory` sem
saber uma da outra, incluindo sobrescrita silenciosa de fato por
coincidência de `subject+predicate`.

O parâmetro `project` (opcional, em `air_store_memory`/`air_register_entity`/
`air_search_context`/`air_get_context`) resolve isso sem separar banco por
projeto (o que mataria o reuso intencional de artefato entre projetos, que
é o objetivo de `air_register_entity`):

- `project=""` (default ao registrar) = **global** — aparece em busca de
  qualquer projeto. Use pra o que é genuinamente reutilizável (uma API já
  pronta, um padrão validado).
- `project="nome"` ao **gravar** = escopa o fato/entidade a esse projeto —
  não supersede nem é sobrescrito por um fato de outro projeto com a mesma
  chave.
- `project="nome"` ao **buscar** = só considera fatos/entidades desse
  projeto MAIS os globais (`project=""`) — some outro projeto escopado não
  aparece.
- Omitir `project` na busca = comportamento antigo, sem filtro, vê tudo
  (inclui o risco de contaminação cross-projeto se quem grava não usar
  escopo).

Eventos (`world/state.py all_events`) também são filtrados por `project`
desde esta versão, mesmo mecanismo (era limitação conhecida documentada
aqui antes, fechada com a mesma migração de schema que `entities` já
tinha — `relations`/`events` ganharam a coluna `project` do mesmo jeito).

**Bug real corrigido, não hipotético**: `air_update_memory` chamava
`memory.remember()` sem passar o `project` do fato original — o default
(`project=""`) fazia o fato *atualizado* nascer **global**, mesmo quando
o original era escopado, e o original ficava `ACTIVE` pra sempre (nunca
virava `SUPERSEDED`, porque a busca interna de "o que supersede" também
usava o `project` errado). Resultado: atualizar um fato de um projeto
vazava o conteúdo pra busca de **qualquer outro** projeto — exatamente a
contaminação cross-projeto que este mecanismo inteiro existe pra evitar,
só que dentro da própria tool que deveria preservá-lo. Corrigido
passando `project=fact.project` explicitamente; `air_update_memory`
agora também devolve `project` no resultado (paridade com
`air_store_memory`, que já devolvia). Testado com reprodução do
vazamento antes da correção e confirmação de que fecha depois
(`tests/test_mcp_server.py::test_update_memory_preserves_project_scope`).

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
`air` como conectado, com as 9 tools acima disponíveis. Sem o Claude
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
| `AIR_ENABLE_SEMANTIC_SEARCH` | `false` | `true` liga a busca semântica opcional (ver seção dedicada abaixo) — desligada por padrão, custo real medido nesta máquina (~187s só de import na 1ª chamada) |

Nenhuma chave de API é lida ou usada por este servidor — ele só fala com
o AIR local (SQLite) e, se `AIR_ENABLE_SEMANTIC_SEARCH=true`, com um
modelo de embeddings local (`sentence-transformers`, baixado uma vez do
Hugging Face Hub e cacheado — sem chave, sem provider de LLM).

### Honestidade metodológica

- **Busca por palavra-chave por padrão, semântica só se pedida e só se
  disponível.** `retrieval.py` pontua por overlap de palavras (substring,
  case-insensitive) entre a query e o texto serializado de cada
  fato/entidade/evento. `"method": "keyword_substring_overlap"` no
  retorno enquanto for só isso; com `AIR_ENABLE_SEMANTIC_SEARCH=true` e o
  modelo carregado com sucesso, vira
  `"hybrid_keyword_substring_and_semantic_embedding:<modelo>"` — o campo
  sempre diz exatamente qual combinação gerou o resultado, nunca afirma
  semântica sem ser (nem finge que é só keyword quando não é).
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
- **Aceleração de busca não muda o que é encontrado.** O índice de
  bissecção descrito em "Aceleração de busca" abaixo só decide *quais*
  registros valem a pena checar — `_score()` continua sendo a única
  fonte de verdade da pontuação, e há teste diferencial comparando
  byte-a-byte contra o scan linear original pra garantir isso.
- **Concorrência**: o SDK MCP despacha cada tool call síncrona num worker
  thread (`anyio.to_thread.run_sync`). Isso quebrava o SQLite (conexões
  são thread-affine por padrão) até ser corrigido com
  `check_same_thread=False` em `world/state.py`/`memory/store.py` + uma
  trava (`threading.Lock`) em `mcp_server/adapter.py` — bug real
  encontrado durante o desenvolvimento, não hipotético, com teste de
  regressão cobrindo a chamada via protocolo MCP de verdade.

### Limitações conhecidas do MCP server

- Busca é palavra-chave por padrão — uma pergunta parafraseada sem
  nenhuma palavra em comum com o fato armazenado não é encontrada, a
  menos que `AIR_ENABLE_SEMANTIC_SEARCH=true` esteja ligado (ver seção
  "Busca semântica opcional" abaixo). Continua desligada por padrão de
  propósito: o custo de import medido (~187s nesta máquina) é grande
  demais pra pagar sem o usuário pedir.
- *Evento* (`Event`) ainda não tem tool MCP de **escrita** — só entidade e
  relação têm (`air_register_entity`, `air_register_relation`). Eventos
  continuam graváveis pela API core (`world.event(...)`, usada
  internamente) e já são consultáveis/filtráveis por `project` via
  `air_search_context`/`air_get_context` — só não há um `air_record_event`
  ainda. Deixado de fora de propósito por ora: evento é histórico auxiliar
  de mudança de entidade (deploy, crash), tipicamente gerado pelo próprio
  runtime observando uma ação, não digitado por um agente via tool call —
  diferente de entidade/relação, que são declaradas deliberadamente.
- `air_register_entity` deduplica por `name` **exato** só na hora de criar
  (não duplica, devolve a existente). Nomes quase-iguais pro mesmo artefato
  (achado real, não hipotético: `air` vs `air-runtime` registrados em
  sessões MCP diferentes pro mesmo projeto) agora geram um aviso no campo
  `possible_duplicates` do retorno — mas ainda **não bloqueia o registro
  nem faz merge automático** (merge errado destrói dado, é pior que viver
  com duplicata) — quem decide o que fazer com o aviso é quem chamou;
  `air_delete_entity` continua sendo o jeito de limpar manualmente.
- ~~`air_register_entity` não faz *update*~~ — resolvido: `air_update_entity`
  atualiza `attrs`/`kind` de uma entidade existente sem apagar e registrar
  de novo (não atualiza `name`/`project` de propósito — mudar isso é
  realisticamente outra entidade, não uma edição da mesma).
- `max_tokens` em `air_get_context` não impede que um único fato maior
  que o orçamento entre sozinho no contexto (a alternativa seria devolver
  contexto vazio mesmo tendo achado algo, o que é pior) — essa parte
  continua deliberada. ~~Mas a checagem de orçamento pra os itens
  seguintes contava só o texto bruto do fato, não o que
  `context.render()` de fato produz~~ — bug real corrigido, achado
  rodando `benchmarks/context_comparison` com dados de verdade (não
  hipotético): o cabeçalho `[kind:id] label` que cada item ganha ao
  renderizar nunca entrava na conta, e com muitos fatos batendo a busca
  o overhead se acumulava até o contexto final passar bem além do
  orçamento pedido — medido: ~939 tokens entregues pra um pedido de 500.
  Corrigido medindo o tamanho *real* renderizado antes de decidir se um
  item cabe (`ContextEngine.delete()`, novo, remove o item especulativo
  se não coube) — ver `tests/test_mcp_server.py::test_get_context_budget_accounts_for_render_overhead_with_many_matches`.
- **Achado mais significativo de todos, rodando o mesmo benchmark**: o
  texto que `air_get_context` monta pra cada fato vinha precedido de um
  cabeçalho `[kind:id] label` (`context/engine.py:render`) — e o `label`
  que `_get_context` passava era literalmente `f"{kind}:{id}"`, ou seja,
  o cabeçalho duplicava DOIS ids sem nenhum valor informativo (nem pra
  quem lê o texto, já que `references` no retorno já carrega
  kind/id/score estruturado). Pro modelo pequeno usado no benchmark
  (SmolLM2-360M-Instruct, único local viável nesta máquina), esse padrão
  de colchetes+ids bagunçava a geração de verdade — medido inspecionando
  as respostas brutas: em vez de responder, o modelo ECOAVA o cabeçalho
  (`"[fact:ctx_1bef5e6163af] fact:fact"`), classificado como resposta
  errada porque genuinamente era. Corrigido com um modo novo de
  `ContextEngine.render(handle_ids, include_headers=False)` (item
  resumido/truncado continua com cabeçalho — ali ele é funcional, diz
  "tem mais, use get(id)" — só o item *pinned*/totalmente incluído perde
  o cabeçalho, que ali nunca teve função nenhuma). Efeito medido, não só
  teorizado: nas mesmas 6 perguntas mais simples do dataset (categoria
  `factual_simple`), a correção sozinha levou de quase todas erradas pra
  5/6 corretas com o modelo real rodando de verdade — maior salto de
  qualidade medido nesta sessão de trabalho no AIR.
- **Cold-start do tokenizer pode ser MUITO mais lento que o esperado.**
  `mcp_server/tokens.py` carrega `AutoTokenizer.from_pretrained(...,
  local_files_only=True)` já com essa flag pra nunca bater na rede — mas
  mesmo assim, medido nesta sessão: a primeira chamada real que precisou
  do tokenizer levou **208 segundos**; a segunda chamada idêntica levou
  **2.9ms** (fica em cache no processo depois do primeiro load). Root
  cause provável: I/O pathológico de disco quase cheio (não confirmado
  como universal, só medido nesta máquina) — mas o efeito é real e sério
  o bastante pra parecer travado: um cliente MCP com timeout menor que
  ~210s vai ver isso como falha, não como lentidão.

  **Mitigado, não eliminado**: `mcp_server/server.py:main()` agora chama
  `tokens.warm_tokenizer_async()` antes de `server.run()` — dispara a
  carga numa thread separada, sem bloquear o handshake MCP (continua
  respondendo na hora, confirmado: warmup síncrono nessa mesma posição
  já tinha sido tentado antes e causava exatamente o "connection timed
  out after 30000ms" que motivou remover o warmup em primeiro lugar). A
  carga roda em paralelo desde o startup, então na prática cobre parte ou
  todo o tempo até a primeira chamada real de verdade acontecer.

  ~~Se a primeira chamada real chegar antes do warmup terminar, ela ainda
  espera o resto do carregamento~~ — resolvido: `_get_tokenizer` agora
  aceita `blocking=False` (usado por `count_tokens()`), que tenta pegar a
  trava sem esperar — se estiver ocupada (warmup em andamento), cai pra'
  heurística na hora em vez de bloquear até o carregamento terminar
  (`method` no retorno continua honesto: `heuristic_chars_div_4` nesse
  caso, nunca finge precisão que não tem). Testado sem depender do
  tokenizer real carregar (`tests/test_tokens.py` segura a trava
  manualmente pra' simular contenção, corre em milissegundos).

## Aceleração de busca — índice de bissecção ("Kakeya")

`mcp_server/kakeya_index.py` acelera `air_search_context`/`retrieval.py`
sem mudar o que ela encontra. Nome "Kakeya" é analogia deliberada, dita
explicitamente no docstring do módulo: o problema da agulha de Kakeya
(Besicovitch/Perron, geometria/teoria da medida — cobrir toda rotação de
uma agulha com área mínima) não tem "algoritmo" de busca nenhum; o que é
reaproveitado de verdade é o princípio por trás da construção de Perron
tree — convergir por **bissecção repetida** em vez de varrer tudo. A
estrutura real, padrão e correta que implementa isso é **array de
sufixos + busca binária** (`bisect`, stdlib): toda substring de um texto
é prefixo de algum sufixo dele, então sufixos que começam com a palavra
buscada formam um intervalo contíguo na ordem alfabética — dois `bisect`
acham esse intervalo em O(log n), sem varrer nada. "3D" mapeia nas três
dimensões reais de dado buscável do AIR (fato/entidade/evento), não é
decorativo.

**O que ele muda de verdade**: antes, `search_facts`/`search_world`
buscavam *todo* fato/entidade/evento visível no SQLite (`all_active`/
`all_entities`/`all_events`) e só depois descartavam quem não pontuava.
Agora, o índice acha primeiro os IDs candidatos (via bissecção) e só
busca no SQLite *esses* IDs (`get_facts_by_ids`/`get_entities_by_ids`/
`get_events_by_ids`, com `WHERE id IN (...)`) — corta o fetch e a
construção de objeto pra quem nunca ia pontuar, não só o `_score()` em
si. `_score()` continua sendo a única fonte de verdade da pontuação:
o índice nunca decide o resultado, só decide o que vale a pena checar.

**Honesto sobre onde isso ajuda de verdade** (medido, não afirmado —
`tests/test_kakeya_index.py::benchmark_index_vs_linear`, mesma disciplina
de `benchmarks/token_benchmark.py`): a primeira versão desta mudança só
acelerava o laço de pontuação e **media pior** que o scan linear original
em corpus grande (0.89x em 20.000 registros) — o custo dominante nunca
foi `_score()` (comparação de substring em C, já barata), foi o fetch +
construção de `Fact`/`Entity`/`Event` do SQLite pra *todo* registro, que
o índice não estava evitando. Corrigido fazendo o índice também podar o
fetch (`WHERE id IN (...)`), não só a pontuação — só então o ganho ficou
real:

| corpus (facts) | query bate em | índice | linear | speedup |
|---:|---:|---:|---:|---:|
| 4.000 | ~28% dos registros | 30,9ms | 84,3ms | 2,7x |
| 20.000 | ~56% dos registros | 424,5ms | 572,7ms | 1,35x |
| 20.000 | 1 registro | 0,67ms | 485,9ms | 722x |

Padrão esperado e confirmado: quanto mais **rara** a palavra buscada
(mais próxima do uso real de busca por palavra-chave), maior o ganho —
o índice pula quase todo o fetch. Quando a query bate em boa parte do
corpus (uso mais parecido com "listar tudo"), o ganho encolhe mas nunca
fica negativo na versão corrigida.

**Trade-off aceito e documentado, não escondido**: cada registro só tem
os primeiros `KAKEYA_MAX_INDEXED_CHARS` (4.000) caracteres do seu texto
buscável indexados — cobre com folga o conteúdo típico do AIR (fato/
entidade curtos), mas um registro mais longo que isso entra em
`overflow_ids()` e é **sempre** tratado como candidato (sem exceção),
voltando pro custo O(n) só pra esses poucos registros em vez de arriscar
um falso negativo — testado explicitamente
(`test_overflow_records_still_found_beyond_indexed_length`).

Correção verificada por teste diferencial, não só pelo benchmark: todo
resultado com índice é comparado byte-a-byte contra o resultado do scan
linear original, em corpus pequeno e em corpus aleatório de 900
registros com 6 palavras de busca diferentes (`tests/test_kakeya_index.py`)
— nenhuma mudança de semântica de busca (substring em qualquer posição,
igual antes) escondida atrás da otimização.

## Busca semântica opcional (`adapters/semantic_search.py`)

Primeiro conteúdo real de `adapters/` (antes vazio — ver "O que ainda
falta"). Fecha a lacuna documentada há duas versões deste README como "a
maior lacuna real de qualidade do projeto": busca por palavra-chave não
encontra uma pergunta parafraseada sem nenhuma palavra em comum com o
fato armazenado. Adota `sentence-transformers` (biblioteca madura, não
reimplementa embeddings do zero — mesma regra de "adotar, não construir"
de `docs/ECOSYSTEM_RESEARCH.md`, que já listava vector search como peça
madura).

**Desligada por padrão** — só liga com `AIR_ENABLE_SEMANTIC_SEARCH=true`.
Motivo medido, não hipotético: nesta máquina, só o
`from sentence_transformers import SentenceTransformer` levou **187
segundos** — mesma patologia de I/O de disco já documentada pro
tokenizer (`mcp_server/tokens.py`, 208s no primeiro load). Se isso
rodasse por padrão no startup do `mcp_server`, reproduziria (ou
pioraria — `sentence-transformers` + `torch` é pilha de dependência bem
maior que só o tokenizer) o mesmo risco de connection timeout que
motivou `tokens.warm_tokenizer_async()` em primeiro lugar. Por isso:

- O import pesado é **lazy** — só acontece dentro de `_get_model()`,
  nunca no topo do módulo. Importar `adapters.semantic_search` (o que
  `mcp_server/retrieval.py` faz sempre) é instantâneo; só chamar
  `embed()` de verdade paga o custo.
- **Sem warmup automático em background** — diferente do tokenizer,
  dado o custo medido (187s só de import), rodar isso numa thread de
  fundo sem o usuário pedir seria gastar CPU/memória da máquina por uma
  feature que ele não ligou. Quem liga a env var paga o custo
  explicitamente, sabendo o número.
- **Fallback gracioso** — se o pacote não estiver instalado ou o load
  falhar (sem rede, disco cheio, etc.), a busca cai pra palavra-chave
  sozinha, sem erro. Uma feature opcional nunca quebra a busca principal.

**Como funciona**: embeddings de cada fato/entidade/evento são
pré-computados uma vez e cacheados (`SemanticIndex`, mesmo padrão de
invalidação por versão do índice Kakeya acima — só reconstrói quando
`WorldState.version()`/`MemoryStore.version()` mudou). Uma busca só
embeda a *query* (uma chamada) e faz produto escalar contra os vetores
já prontos — reembedar o corpus inteiro a cada busca seria proibitivo.

**Achado real durante o desenvolvimento, não hipotético**: o texto
embedado NÃO é o mesmo texto compacto que `_score()` usa pra palavra-
chave (`"subject predicate = obj"`, formato dict pros attrs de
entidade). Primeira tentativa usou esse texto direto e o teste de
paráfrase (ver abaixo) falhou — medido: com o wrapper completo a
similaridade caiu pra 0.32 (abaixo do threshold), contra 0.37 embedando
só o conteúdo em linguagem natural (`f.obj`, sem prefixo de
`subject`/`predicate`, sem "="). Um token tipo `cache-01` e sintaxe de
dict são ruído pra um encoder treinado em frase natural, não sinal — por
isso `_fact_semantic_text`/`_entity_semantic_text`/`_event_semantic_text`
existem separados de `_fact_text`/`_entity_text`/`_event_text`: a busca
por palavra-chave continua vendo o texto compacto de sempre (nenhuma
mudança lá), só o que é embedado é diferente.

Resultado final é híbrido: `score = keyword_score + semantic_score`
(cosine similarity, 0..1) — um match forte por palavra-chave continua
pesando mais que um match semântico fraco, mas um registro com **zero**
palavra em comum ainda pode entrar se a similaridade passar de
`SEMANTIC_MATCH_THRESHOLD` (0.35, calibrado contra um par parafraseado
real medido, não um número inventado — ver docstring do módulo). Cada
resultado do modo híbrido inclui `keyword_score`/`semantic_score`
separados no `metadata`, e o campo `method` do retorno diz exatamente
`hybrid_keyword_substring_and_semantic_embedding:<modelo>` — nunca
afirma semântica sem ser (mesma disciplina de honestidade do resto do
projeto).

**Testado com o caso que motiva a feature**, não só com o mecanismo
isolado (`tests/test_semantic_search.py` — separado da suite rápida de
propósito, mesmo motivo do benchmark acima): um fato ("o serviço de
cache roda sobre redis em produção") e uma pergunta parafraseada
("qual tecnologia database usada para armazenamento rápido") com
**overlap de palavra confirmado como zero** (`_score() == 0`, checado no
próprio teste antes de usar a busca semântica — sem essa garantia, um
match "achado" podia só ser keyword coincidindo, não provaria nada) —
keyword puro não encontra (testado explicitamente que dá `[]`), híbrido
encontra. Suite roda com `AIR_ENABLE_SEMANTIC_SEARCH=true python
tests/test_semantic_search.py`; sem a env var, os testes que dependem
do modelo pulam (`[SKIP]`), não falham — rodar sem querer não trava a
suite rápida nem baixa nada.

## Benchmark comparativo — AIR vs. truncation/keyword/semantic-RAG/LLMLingua

`benchmarks/context_comparison/` é um harness bem mais rigoroso que o
benchmark de token acima: compara AIR (prosa e estrutural, via
`mcp_server/adapter.py` de produção, sem alterar lógica pra favorecer o
teste) contra full-context (baseline), truncation em 3 limites, keyword
retrieval (baseline lexical independente, top-3 sentenças), semantic RAG
(embeddings), LLMLingua/LongLLMLingua/LLMLingua-2 — todas rodando o
*mesmo* modelo final (SmolLM2-360M-Instruct local, sem chave de API) para
não misturar "qual abordagem" com "qual modelo". Dataset sintético
determinístico (seed fixa), 6 categorias que testam capacidades
diferentes (`factual_simple`, `multi_hop`, `recency_conflict`,
`long_distance`, `irrelevant_context`, `repeated_information`) — ver
`benchmarks/context_comparison/datasets/synthetic.py`.

**Rodado a 96 casos** (`n_per_category=16`, dobro do default — "mais do
mesmo", pedido explícito do usuário), resultado real
(`benchmarks/context_comparison/reports/run_n16_summary.json`):

| abordagem | acurácia | tokens médios | economia | ganho de qualidade |
|---|---:|---:|---:|---:|
| full_context (baseline) | 0,573 | 318,7 | — | 1,000 |
| keyword_retrieval | 0,573 | 145,7 | 173,0 tok (54,3%) | 1,000 |
| semantic_rag | **0,729** | 146,7 | 172,0 tok (54,0%) | **1,273** |
| **air_structural_memory** | 0,604 | 291,5 | 27,1 tok (8,5%) | 1,055 |
| air (prosa) | 0,469 | 292,6 | 26,1 tok (8,2%) | 0,818 |
| llmlingua2 (n=12) | 0,417 | 180,8 | 137,8 tok (43,3%) | 0,727 |

**A primeira corrida (n=8/categoria, antes desta versão) mostrava o AIR
pior que TODAS as alternativas** — economia de token *negativa*
(usava mais token que o contexto bruto) e acurácia bem abaixo do
baseline. Investigado a fundo, não aceito como "ruído do modelo pequeno"
sem checar — dois bugs reais, achados e corrigidos nesta sessão (ver
"Limitações conhecidas" acima para os dois com todo o raciocínio):

1. Orçamento de token (`max_tokens`) contava errado, entregando quase o
   dobro do pedido.
2. O cabeçalho `[kind:id] label` que cada fato ganhava no texto
   renderizado (dois ids sem sentido nenhum) fazia o modelo pequeno
   *ecoar o cabeçalho* em vez de responder — a causa real, maior que a
   do orçamento, confirmada lendo as respostas brutas do modelo, não
   inferida da métrica agregada.

Com os dois corrigidos, `air_structural_memory` foi de pior-que-tudo pra
**acurácia acima do baseline (0,604 vs 0,573) usando menos token** —
uma reversão real, não um ajuste de threshold pra melhorar número.

**Honesto sobre o que ainda não é verdade**: mesmo corrigido, o AIR
continua *atrás* de `keyword_retrieval`/`semantic_rag` em economia de
token (27 vs ~173 tokens salvos) e mais ou menos empatado ou levemente
atrás em acurácia agregada — essas duas abordagens usam um corte fixo
(top-3 sentenças, ou top-k por similaridade) enquanto o AIR ainda inclui
*todo* fato com `score > 0` até o orçamento acabar, o que deixa mais
ruído de baixa relevância no contexto final do que precisaria. Isso é
uma lacuna real e não corrigida ainda — próximo candidato a melhoria,
não escondido aqui pra fazer o número parecer melhor. Por categoria
(`run_n16_by_category.json`), o AIR estrutural *vence* claramente em
`long_distance` (0,938 — melhor de todos) e é competitivo em
`irrelevant_context`/`repeated_information`, mas fica atrás em
`multi_hop` e `recency_conflict` (esta última é, ironicamente, a
categoria desenhada especificamente pra testar o mecanismo de
recência/supersede do AIR — resultado real, registrado sem embelezar).

Reprodutível: `python -m benchmarks.context_comparison.runners.run --offline`
(a partir de `E:\x\air`, com `requirements-optional.txt` instalado —
custa horas de CPU local nesta máquina, ver docstring do runner).

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

`adapters/` ganhou seu primeiro conteúdo real nesta versão
(`adapters/semantic_search.py`, ver seção dedicada acima) — mas o resto
da lista de "adotar, não construir" da pesquisa original continua não
implementado: Firecracker/E2B (sandbox), Temporal (durabilidade) e
Graphiti (grafo de memória); nenhum tem conta/serviço configurado nesta
máquina pra testar de verdade sem inventar resultado. `tools/registry.py`
também continua agnóstico de protocolo — o AIR ainda só é *exposto* via
MCP, não *consome* tool externa via MCP ele mesmo (as duas direções são
independentes, ver seção MCP Server acima). O SDK JS/TS também não existe
ainda (pedido explícito do usuário: Python primeiro).

Desde a versão anterior deste README, ficaram prontos e testados:
aceleração de busca por índice de bissecção (`mcp_server/kakeya_index.py`
— ver "Aceleração de busca" acima, com o achado honesto de que a
primeira versão media pior que o scan linear original até o fetch
também ser podado, não só a pontuação); e busca semântica opcional via
embeddings locais (`adapters/semantic_search.py` — ver "Busca semântica
opcional" acima), a lacuna que este README apontava havia duas versões
como "a maior lacuna real de qualidade do projeto". Continua desligada
por padrão (custo de import medido, ~187s nesta máquina) — quem quiser
ligada paga o custo sabendo o número, não escondido.

A lacuna de maior impacto que continua real agora: os três adapters de
`docs/ECOSYSTEM_RESEARCH.md` que dependem de infraestrutura externa
(Firecracker/E2B, Temporal, Graphiti) — nenhum pode ser implementado
honestamente sem uma conta/serviço real pra testar contra, e nenhum
existe nesta máquina.
