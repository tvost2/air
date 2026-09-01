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
from mcp.types import ToolAnnotations

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
    # instructions: guia de USO pro LLM (diferente de description, que e'
    # so' o resumo pra' catalogo/UI) -- campo MCP separado, existe na SDK
    # mas nao estava sendo usado. Sintetiza o padrao operacional que
    # antes so' vivia espalhado nas docstrings de cada tool + no prompt
    # reconstruct_context.
    instructions=(
        "Antes de reconstruir contexto do zero ou pedir informacao ja' "
        "fornecida antes nesta sessao/projeto, chame air_search_context "
        "(barato, so' consulta) pra' ver se ja' existe. Se existir e' "
        "so' o que precisa, use o resultado direto; se precisar do fluxo "
        "completo (recencia/conflito resolvidos + orcamento de tokens), "
        "chame air_get_context.\n\n"
        "Depois de CONSTRUIR algo reutilizavel (API, modulo, servico), "
        "registre com air_register_entity ANTES de encerrar a tarefa -- "
        "e' o que permite uma sessao futura achar e readaptar em vez de "
        "reescrever do zero. Ligue entidades relacionadas com "
        "air_register_relation (ex: 'X depends_on Y') pra' "
        "air_search_context/dependents_of responder perguntas de "
        "impacto ('o que quebra se eu mudar Y?').\n\n"
        "Sempre que a sessao tiver um projeto identificavel, passe "
        "project=<nome> em toda tool que aceita -- sem isso, todo fato/"
        "entidade fica GLOBAL (visivel a qualquer projeto) por padrao, "
        "risco real e ja observado de contaminacao cross-projeto quando "
        "duas sessoes MCP paralelas compartilham o mesmo storage.\n\n"
        "Tools marcadas destructive_hint (air_delete_entity, "
        "air_delete_memory) nao tem desfazer -- confirme antes de "
        "chamar se a intencao nao for inequivoca. Todo resultado de "
        "busca declara seu 'method' (keyword_substring_overlap, ou "
        "hybrid_... se busca semantica estiver ligada) -- nao assuma "
        "mais precisao do que o metodo realmente entrega."
    ),
    version="0.1.0",
)


@server.tool(
    title="Buscar contexto na memória AIR",
    # Anotacoes MCP -- classificacao honesta baseada no comportamento
    # real de cada tool (verificado em mcp_server/adapter.py, nao
    # suposto), pra' o cliente MCP tomar decisao melhor (ex: pedir
    # confirmacao antes de tool destrutiva, saber que e' seguro tentar de
    # novo). readOnlyHint=True: so' consulta, retrieval.search() nunca
    # escreve. idempotentHint=True: mesma query com o mesmo storage
    # devolve o mesmo resultado. openWorldHint=False: so' fala com o
    # SQLite local (nunca rede -- ver README "Nenhuma chave de API...").
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False),
)
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


@server.tool(
    title="Armazenar fato na memória AIR",
    # idempotentHint=False verificado: MemoryStore.remember() SEMPRE
    # insere uma linha nova (mesmo com subject/predicate/content
    # identicos, a chamada de novo supersede a anterior e cria mais uma
    # versao no historico) -- chamar duas vezes NAO tem o mesmo efeito de
    # chamar uma vez, entao seria desonesto marcar idempotente.
    # destructiveHint=False: a versao anterior nunca e' apagada, so'
    # marcada SUPERSEDED.
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False),
)
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


@server.tool(
    title="Registrar entidade no World State",
    # idempotentHint=True verificado: chamar de novo com o mesmo `name`
    # NAO cria duplicata, devolve a entidade existente
    # (already_existed=true no retorno) -- estado final converge, mesmo
    # que o campo already_existed mude entre a 1a e a 2a chamada.
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False),
)
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


@server.tool(
    title="Remover entidade do World State",
    # destructiveHint=True: hard delete de verdade (Entity nao tem
    # soft-delete como Fact -- ver world/state.py:delete_entity), sem
    # tool de desfazer. idempotentHint=False verificado: chamar de novo
    # sobre um id ja' deletado devolve {"error": ...} (diferente do
    # {"deleted": true} da 1a chamada) -- o retorno muda entre chamadas,
    # entao nao e' seguro pra' quem chama assumir repeticao sem custo.
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False),
)
def air_delete_entity(id: str) -> dict:
    """Remove uma entidade do World State por id (hard delete -- Entity
    nao tem versao/historico como Fact, entao nao ha' o que preservar).
    Use pra' corrigir registro enganado ou duplicata: air_register_entity
    so' deduplica por 'name' EXATO, entao nomes quase-iguais pro mesmo
    artefato (ex: 'air' vs 'air-runtime') viram entidades separadas --
    esta tool e' o jeito de limpar isso manualmente ate' existir merge
    automatico (nao existe ainda)."""
    return adapter.delete_entity(id)


@server.tool(
    title="Atualizar entidade do World State",
    # idempotentHint=True: os mesmos argumentos aplicados duas vezes
    # convergem pro mesmo estado final (merge ou replace do mesmo attrs
    # duas vezes = igual a uma vez). destructiveHint=True: com
    # merge_attrs=False, attrs anteriores nao inclusos no novo dict sao
    # PERDIDOS (substituicao total, nao merge) -- classificado como
    # destrutivo porque a tool PODE perder dado dependendo do argumento,
    # nao so' quando os dois lados concordam em nunca usar False.
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False),
)
def air_update_entity(id: str, attrs: dict | None = None, kind: str | None = None, merge_attrs: bool = True) -> dict:
    """Atualiza kind e/ou attrs de uma entidade ja' registrada, sem
    precisar apagar e registrar de novo. Preserva id/name/project/
    created_at -- so' conteudo muda.

    attrs: merge_attrs=True (default) combina com os attrs existentes
    (chave repetida usa o valor novo, o resto e' preservado);
    merge_attrs=False substitui attrs inteiro pelo que foi passado.
    kind: opcional, so' muda se informado.
    Nao atualiza name nem project de proposito -- mudar isso e'
    realisticamente registrar outra entidade (delete_entity +
    register_entity), nao uma edicao da mesma."""
    return adapter.update_entity(id, attrs=attrs, kind=kind, merge_attrs=merge_attrs)


@server.tool(
    title="Registrar relação entre entidades",
    # idempotentHint=False verificado: _register_relation() nao faz
    # dedup nenhum -- chamar duas vezes com os mesmos source_id/kind/
    # target_id cria DUAS relacoes separadas (ids diferentes), diferente
    # de air_register_entity que dedupa por name.
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False),
)
def air_register_relation(source_id: str, kind: str, target_id: str, project: str = "") -> dict:
    """Registra uma relacao entre duas entidades JA' registradas em World
    State (ex: source_id="api", kind="depends_on", target_id="database").
    E' o que faz world.dependents_of() (usado internamente por
    air_get_context) responder "o que depende de X" por consulta direta.

    source_id/target_id: precisam ser ids de entidades ja' existentes
    (registradas antes com air_register_entity) -- retorna erro claro se
    algum dos dois nao existir, em vez de criar uma relacao apontando pro
    vazio.
    project: mesmo mecanismo de escopo das outras tools -- "" (default) =
    relacao global, um nome de projeto restringe."""
    return adapter.register_relation(source_id, kind, target_id, project=project)


@server.tool(
    title="Reconstruir contexto completo",
    # readOnlyHint=True: _get_context() roda Planner sobre
    # retrieval.search() + montagem de texto -- nenhuma etapa escreve em
    # world/memory (verificado lendo adapter.py:_get_context). Mesma
    # classificacao de air_search_context.
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False),
)
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


@server.tool(
    title="Atualizar fato na memória AIR",
    # idempotentHint=False: mesma logica de air_store_memory --
    # memory.remember() sempre insere versao nova, chamar duas vezes com
    # o mesmo content cria duas versoes no historico (nao um no-op).
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False),
)
def air_update_memory(id: str, content: str) -> dict:
    """Atualiza uma memoria existente por id, criando uma nova versao que
    supersede a antiga (preserva o historico, nao sobrescreve em lugar --
    mesmo mecanismo de recencia usado no resto do AIR)."""
    return adapter.update_memory(id, content)


@server.tool(
    title="Remover fato da memória AIR",
    # destructiveHint=True: apesar de ser soft-delete no storage (linha
    # fica, so' marcada DELETED -- ver README/memory/store.py), nao ha'
    # tool de desfazer, entao do ponto de vista de quem chama o efeito e'
    # irreversivel -- mesmo criterio conservador de air_delete_entity.
    # idempotentHint=False verificado: chamar de novo sobre id ja'
    # deletado devolve {"error": "...ja' estava deletada"}, retorno
    # diferente da 1a chamada.
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False),
)
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
