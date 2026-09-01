"""
AIR mcp_server -- unica parte deste projeto que conhece o protocolo MCP.

Camada FINA de proposito (regra 2 do pedido): so' registra tools/
resources/prompts e delega pra' mcp_server/adapter.py, que e' onde a
logica de verdade mora (e que continua 100% testavel/usavel sem MCP).

SDK usado: 'mcp' (oficial, pip), versao 2.1.0 instalada nesta maquina --
API inspecionada diretamente no pacote instalado antes de escrever este
arquivo (a classe de alto nivel nesta versao chama-se MCPServer, nao
FastMCP como em versoes mais antigas do SDK que existem por ai' em
tutorial desatualizado -- confirmado via `dir(mcp.server)` nesta maquina,
nao suposto).

CRITICO pra' transporte stdio: stdout e' o canal do protocolo JSON-RPC.
Nenhum print() pode ir pra' stdout neste processo -- todo log vai pra'
stderr via logging padrao (regra implicita de qualquer servidor MCP
stdio, nao documentada explicitamente no pedido mas necessaria pra'
funcionar de verdade com o Claude Code).
"""
from __future__ import annotations

import logging
import sys

from mcp.server import MCPServer

from mcp_server import tokens
from mcp_server.adapter import AirAdapter
from mcp_server.config import config

logging.basicConfig(
    level=getattr(logging, config.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,   # nunca stdout -- ver docstring do modulo
)
logger = logging.getLogger("air.mcp_server")

adapter = AirAdapter(config)

server = MCPServer(
    name="air",
    title="AIR Context Server",
    description=(
        "Camada externa de memoria/context-retrieval do projeto AIR. "
        "NAO substitui o contexto interno do Claude -- complementa "
        "consultando fatos estruturados (preferencia, responsabilidade, "
        "regra com recencia) e estado de projeto (entidade/relacao/evento) "
        "persistidos entre sessoes, evitando reenviar informacao que ja' "
        "existe de forma consultavel."
    ),
    version="0.1.0",
)


@server.tool()
def air_search_context(query: str, limit: int = 5, project: str = "") -> dict:
    """Busca contexto relevante na memoria estruturada e no estado de
    projeto do AIR (busca por palavra-chave, nao semantica -- ver campo
    'method' no retorno). Use pra' descobrir SE existe informacao
    relevante antes de pedir reconstrucao completa com air_get_context.

    project: opcional. Sem informar, busca em TODOS os projetos (inclui
    risco de contaminacao cross-projeto -- ver README, secao Isolamento).
    Informando (ex: project="fractionengine"), a busca so' considera
    fatos/entidades desse projeto MAIS os marcados como globais
    (registrados sem project) -- e' o mecanismo de isolamento entre
    "mundos"/sessoes diferentes."""
    return adapter.search_context(query, limit=limit, project=project or None)


@server.tool()
def air_store_memory(content: str, metadata: dict | None = None) -> dict:
    """Armazena uma informacao/fato na memoria estruturada do AIR, com
    recencia automatica: se 'metadata' incluir subject/predicate iguais a
    um fato ja' existente NO MESMO project, a nova versao SUPERSEDE a
    antiga (a antiga fica no historico, nao e' apagada). Sem subject/
    predicate explicitos, cria uma nota nova e independente a cada
    chamada.

    metadata aceita: subject, predicate, reason, e project (opcional --
    "" ou omitido = fato global, visivel em busca de qualquer projeto;
    um nome de projeto escopa o fato pra' so' aparecer em busca feita com
    o mesmo project=, protegendo contra contaminacao/sobrescrita
    silenciosa entre projetos diferentes)."""
    return adapter.store_memory(content, metadata=metadata)


@server.tool()
def air_register_entity(kind: str, name: str, attrs: dict | None = None, project: str = "") -> dict:
    """Registra no World State um artefato JA' CONSTRUIDO (API, frontend,
    modulo, servico) pra' que uma tarefa futura ache via
    air_search_context/air_get_context e READAPTE em vez de reescrever do
    zero -- e' o mecanismo pensado pra' evitar retrabalho entre sessoes/
    projetos.

    kind: categoria livre (ex: "api", "frontend", "modulo", "servico").
    name: identificador unico -- registrar de novo com o mesmo name NAO
    atualiza attrs, so' devolve a entidade existente (already_existed=true
    no retorno); nao ha' tool de update de entidade ainda.
    attrs: metadados livres (recomendado: path, description, stack,
    expose/capabilities) -- o retorno ja' inclui o custo real em tokens
    (tokenizador real quando disponivel, ver campo _context_cost_method)
    de trazer esta entidade de volta ao contexto se for reusada.
    project: "" (default) = entidade global, aparece em busca de
    QUALQUER projeto -- use isso pra' artefatos genuinamente reutilizaveis
    entre projetos. Um nome de projeto restringe a entidade a busca feita
    com esse mesmo project=."""
    return adapter.register_entity(kind, name, attrs=attrs, project=project)


@server.tool()
def air_delete_entity(id: str) -> dict:
    """Remove uma entidade do World State por id (hard delete -- Entity
    nao tem versao/historico como Fact, entao nao ha' o que preservar).
    Use pra' corrigir registro enganado ou duplicata: air_register_entity
    so' deduplica por 'name' EXATO, entao nomes quase-iguais pro mesmo
    artefato (ex: 'air' vs 'air-runtime') viram entidades separadas --
    esta tool e' o jeito de limpar isso manualmente ate' existir merge
    automatico (nao existe ainda)."""
    return adapter.delete_entity(id)


@server.tool()
def air_get_context(query: str, max_tokens: int | None = None, project: str = "") -> dict:
    """Reconstroi o contexto minimo necessario pra' responder a query,
    usando o Planner do AIR (retrieval -> resolucao de recencia/conflito
    -> montagem final respeitando o orcamento de tokens). Retorna o texto
    de contexto pronto pra' uso, as referencias usadas, e accounting real
    de tokens (metodo do tokenizador informado no retorno).

    project: mesmo mecanismo de escopo de air_search_context -- "" busca
    tudo, um nome de projeto restringe a esse projeto + entidades/fatos
    globais."""
    return adapter.get_context(query, max_tokens=max_tokens, project=project or None)


@server.tool()
def air_update_memory(id: str, content: str) -> dict:
    """Atualiza uma memoria existente por id, criando uma nova versao que
    supersede a antiga (preserva o historico, nao sobrescreve em lugar --
    mesmo mecanismo de recencia usado no resto do AIR)."""
    return adapter.update_memory(id, content)


@server.tool()
def air_delete_memory(id: str) -> dict:
    """Remove (soft-delete) uma memoria especifica por id. A memoria para
    de aparecer em buscas, mas o registro permanece no storage marcado
    como deletado (auditoria)."""
    return adapter.delete_memory(id)


@server.resource("air://memory/facts")
def memory_facts_resource() -> str:
    """Snapshot JSON de todos os fatos ATIVOS na memoria -- 'contextos
    recuperaveis' (regra 5)."""
    import json
    facts = adapter.memory.all_active()
    return json.dumps([
        {"id": f.id, "subject": f.subject, "predicate": f.predicate, "obj": f.obj, "reason": f.reason, "created_at": f.created_at}
        for f in facts
    ], ensure_ascii=False, indent=2)


@server.resource("air://world/state")
def world_state_resource() -> str:
    """Snapshot JSON do World State (entidades + eventos recentes) --
    'estado do projeto' (regra 5)."""
    import json
    entities = adapter.world.all_entities()
    events = adapter.world.all_events(limit=100)
    return json.dumps({
        "entities": [{"id": e.id, "kind": e.kind, "name": e.name, "attrs": e.attrs} for e in entities],
        "recent_events": [{"id": ev.id, "entity_id": ev.entity_id, "kind": ev.kind, "payload": ev.payload, "created_at": ev.created_at} for ev in events],
    }, ensure_ascii=False, indent=2)


@server.prompt()
def reconstruct_context(query: str) -> str:
    """Template pra' orientar reconstrucao de contexto com resolucao
    explicita de conflito/recencia (regra 5: 'instrucoes para resolucao
    de conflitos', 'templates para recuperacao de contexto')."""
    return (
        f"Use air_get_context(query={query!r}) pra' recuperar contexto "
        "estruturado do AIR antes de responder. Se o resultado incluir "
        "mais de uma versao do mesmo fato (mesmo subject+predicate), a "
        "versao mais recente (maior created_at, sem superseded_id "
        "apontando pra' ela) e' a que vale -- o AIR ja' resolve isso "
        "automaticamente em air_get_context, entao normalmente so' uma "
        "versao aparece. Se o campo 'context' vier vazio, significa que "
        "nao ha' informacao relevante armazenada, nao que a busca falhou."
    )


def main():
    logger.info("AIR MCP server iniciando -- storage=%s", config.storage_path)
    # SEM warmup SINCRONO aqui de proposito: rodar tokens.count_tokens()
    # (~208s medido nesta maquina na carga fria, so' carregando do cache
    # local) ANTES de server.run() bloqueava o handshake inicial MCP -- o
    # cliente (Claude Code) tem timeout de conexao de 30s, e o processo
    # nao respondia nada nesse intervalo porque ainda nao tinha comecado
    # a ouvir stdio. Resultado real observado: "connection timed out
    # after 30000ms".
    #
    # Mas SEM warmup nenhum, quem paga os ~208s e' a primeira chamada
    # REAL de air_get_context/air_search_context, de forma sincrona e sem
    # aviso -- ainda pode estourar o timeout de tool call de quem estiver
    # chamando. warm_tokenizer_async() dispara a carga numa thread
    # separada agora, ANTES de server.run(): a resposta ao handshake nao
    # espera essa thread (continua imediata), mas a carga ja' esta'
    # rodando em paralelo desde o startup em vez de so' comecar quando o
    # primeiro tool call real chegar -- na pratica cobre grande parte (ou
    # todo) o tempo entre "servidor conectado" e "agente faz a primeira
    # chamada de verdade".
    tokens.warm_tokenizer_async()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
