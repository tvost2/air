# ECOSYSTEM_RESEARCH — AIR (AI Runtime), Fase 1

Pesquisa profunda do ecossistema (2026) antes de qualquer decisão de
arquitetura ou implementação, conforme pedido explícito: identificar o que
já existe e é maduro o suficiente pra **adaptar**, e o que continua sendo
**lacuna real** que o AIR precisaria resolver do zero.

Metodologia: 4 pesquisas paralelas independentes, cada uma cobrindo 3
domínios técnicos, com busca real na web e fonte citada por afirmação
factual. Não é achismo nem conhecimento estático — é pesquisa de verdade,
datada de 2026.

## 1. O que já é maduro — adaptar, não reimplementar

| Componente | Adotar | Por quê |
|---|---|---|
| **Tool Registry / protocolo de ferramenta** | **MCP** | Doado à Linux Foundation (Agentic AI Foundation) em dez/2025, co-fundado por Anthropic+OpenAI+Block; 97M downloads/mês; OpenAI descontinuou a própria Assistants API em favor dele. Padrão de fato do setor, não aposta arriscada. |
| **Browser tool** | **Playwright + `@playwright/mcp`** (Microsoft, Apache-2.0) | Já expõe ~40 tools via MCP usando accessibility tree; suportado nativamente por Claude Code, Cursor, VS Code. Não vale reimplementar. |
| **Sandbox/isolamento de processo** | **Firecracker (via E2B) ou gVisor (via Modal)** | Consenso técnico real: "containers puros não bastam pra código não confiável, microVM é a única camada produção-segura hoje". APIs prontas (create sandbox / run code / filesystem API). |
| **Model provider abstraction** | **LiteLLM** (self-hosted) | 100+ provedores incl. Ollama/vLLM local, 40k+ estrelas. Ressalva real: ataque de supply chain em mar/2026 (versões PyPI comprometidas) e uma CVE de bypass de auth — usar com atenção de segurança, não cegamente. |
| **Durabilidade / checkpoint / retry** | **Temporal.io** | Integração oficial GA com OpenAI Agents SDK, Vercel AI SDK e Google ADK. Checkpoint automático por step, retoma de onde parou. Resolve bem durabilidade *mecânica* — não decide se a ação funcionou (ver lacunas). |
| **Vector DB** | **Qdrant** (performance) / **LanceDB** (embarcado, MVP, in-process sem servidor) / **pgvector** (simplicidade) | Todos maduros; escolha depende do caso, não precisa inventar mais um. |
| **Grafo de conhecimento temporal (memória)** | **Graphiti** (motor open-source por trás do Zep) | Bitemporal (quando o fato era verdade vs. quando foi ingerido), resolução automática de conflito, retrieval <200ms independente do tamanho do grafo. 94.8% no DMR, -90% latência no LongMemEval vs. MemGPT. |
| **Extração de fato discreto de conversa** | **Mem0** | Já confirmado em pesquisa anterior desta sessão: extrai fato, resolve ADD/UPDATE/DELETE/NONE, 92.5 LoCoMo / 94.4 LongMemEval, <7000 tokens/chamada. |
| **Observabilidade/tracing** | **Langfuse** (MIT, self-hosted, OTel-based) | Alternativa fechada (LangSmith) é ~25x mais cara e self-host só em Enterprise. Helicone entrou em modo manutenção após aquisição. |
| **Interop entre agentes** | **A2A (Agent2Agent)** | Transferido à Linux Foundation em 2025, adotado cross-framework (LangGraph, ADK, CrewAI já falam A2A). |

## 2. Lacunas reais — o AIR precisaria construir, ninguém resolveu bem

Esta é a parte que mais importa: **as duas peças mais centrais da proposta
original do AIR (Context Engine e World State) caem exatamente nas
lacunas confirmadas pela pesquisa** — não é reinventar roda, é preencher
buraco real.

### 2.1 Context Engine (estado de trabalho/sessão ativa) — **território praticamente livre**

Não existe projeto dedicado e maduro de "context engine" com adoção
ampla. "Context engineering" em 2026 é tratado como *prática de design*,
não produto — LlamaIndex chega mais perto mas é focado em RAG, não em
estado de agente. Bibliotecas de compressão pura (LLMLingua, Morph
Compact) existem mas sofrem de "generalization gap" fora da distribuição
de treino, não são solução geral. **Nenhum projeto sério ataca
especificamente "referenciar objeto grande por ID em vez de serializar
tudo" como abstração reutilizável.** Isso é literalmente o "Context
Engine" que o AIR propôs — não achamos nada equivalente pronto.

### 2.2 World State (entidade/relação/evento consultável fora do prompt) — **confirmado como lacuna real**

Tudo que existe (Graphiti, Cognee, Microsoft GraphRAG, Neo4j-agent-memory)
é framework de **memória/retrieval** — otimizado pra responder "o que o
agente lembra", não "o que depende de X agora" como primitiva de
execução consultável pelo Planner. A referência mais próxima (Agent World
Model da Snowflake) é voltada a gerar ambientes sintéticos pra treino de
RL, não runtime de produção. Confirma a tese central do AIR.

### 2.3 Verificação semântica de sucesso de ação + rollback automático

Temporal resolve durabilidade mecânica (retry até dado válido, checkpoint
por step) mas **não decide se a ação teve sucesso semântico**. Pesquisa
acadêmica recente (ACL 2026) mostrou que os métodos comuns de detecção de
falha por confiança do modelo tiveram desempenho **próximo de aleatório**.
Survey de 2026: nenhum dos 12 frameworks avaliados garante semântica
exactly-once na fronteira de tool call. Dado de mercado: 30% das execuções
autônomas de agente exigem alguma recuperação, e "ausência de
monitoramento/rollback" é causa dominante de falha em produção.

### 2.4 Permissão granular composável por capacidade

Nenhum provedor de sandbox (E2B, Modal, Daytona) modela um sistema
reutilizável de permissão por-ferramenta (READ/WRITE/EXECUTE/NETWORK/
DATABASE/FILESYSTEM/BROWSER). E2B nem tem filtragem de egress de rede.
Isolamento de processo está resolvido (Firecracker/gVisor); modelagem de
permissão por capacidade não está.

### 2.5 Planejador de tarefa standalone maduro

Não existe biblioteca de propósito geral, produção, separada de framework
de agente completo, análoga a "PDDL para LLM agents genéricos". Só papers
acadêmicos (CORPGEN, Goal2Skill, MiRA) e integrações PDDL específicas de
robótica. O ciclo de verificação em si já tem nome/padrão documentado
(ReAct → Reflexion → Co-ReAct, uma extensão de 2026 que formaliza
Reason-Act-Verify-Observe) — vale adotar a nomenclatura, mas nenhum vira
biblioteca de produção.

### 2.6 Protocolo de memória universal vendor-neutral

O MCP propositalmente não define primitiva de memória (só tools/resources/
prompts/sampling) — gerou implementações fragmentadas. Existe um candidato
acadêmico recente e genuinamente relevante: **memorywire** (arXiv
2606.01138), wire format JSON-Schema para remember/recall/forget/merge/
expire, com adapters de referência pra sqlite-vec/Mem0/Letta/Cognee/
pgvector — mas é paper recente com pacote PyPI v0.4.0, **promissor, não
padrão de indústria ainda**. Vale observar, não apostar total nele.

### 2.7 Abstração madura tipo "LiteLLM para vector DB"

Não existe consolidada. Vextra (arXiv 2601.06727) é proposta acadêmica,
não produto adotado. LiteLLM em si só cobre embeddings multi-provider, não
roteamento de vector DB.

### 2.8 Schema padrão de trace de agente

Três formatos concorrentes (OTel GenAI Semantic Conventions — ainda
experimental/pré-1.0 mesmo em 2026 —, OpenInference da Arize, OpenLLMetry
da Traceloop), incompatíveis em detalhe, nenhum dominante. O AIR precisa
compor algo por cima de um deles (provavelmente OTel, por ser o mais
neutro), não copiar de um único projeto.

## 3. Síntese executiva — o que isso significa pra arquitetura do AIR

**Adaptar sem dó:** MCP (tool registry), Playwright/playwright-mcp
(browser), Firecracker/E2B ou gVisor/Modal (sandbox), LiteLLM
(model provider, com cautela de segurança), Temporal (durabilidade),
Qdrant/LanceDB (vector), Graphiti (grafo de memória), Mem0 (extração de
fato), Langfuse (observabilidade), A2A (interop).

**Construir de verdade, porque é lacuna confirmada, não capricho:**
Context Engine (montagem de contexto mínimo, referência por ID) e World
State (entidade/relação/evento consultável fora do prompt) — exatamente
as duas peças que a proposta original do AIR colocou no centro. Também:
Verification Engine (decisão semântica de sucesso, não só retry mecânico),
modelo de permissão granular por capacidade, e Planner (não existe nada
maduro pra adaptar).

**Isso muda a arquitetura em um ponto importante:** boa parte do
diagrama original do AIR (Tool Registry, Filesystem, Browser Runtime,
Process Runtime, Security/Sandbox) deveria ser **camada fina de adapter
sobre tecnologia madura já existente**, não implementação própria. O
esforço de engenharia real deveria se concentrar em Context Engine, World
State, Verification Engine e Planner — as quatro peças onde a pesquisa
confirma que não existe nada bom pra copiar.
