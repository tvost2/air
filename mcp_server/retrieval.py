"""
AIR mcp_server -- camada de retrieval.

Nao existia um modulo de retrieval no AIR core antes deste trabalho (a
inspecao confirmou: memory/store.py so' faz lookup exato por
subject+predicate, world/state.py so' faz lookup por id/nome exatos --
nenhum dos dois faz busca por palavra-chave sobre TUDO). E' a
implementacao minima necessaria (regra 11 do pedido do usuario: "se nao
existir persistencia/retrieval adequada, criar o minimo necessario, com
interface abstrata") -- nao um motor de busca novo, so' as funcoes de
consulta que faltavam por cima do que world/state.py e memory/store.py
ja' expoem.

Honestidade metodologica (mesma disciplina desta sessao inteira,
struct-reasoning/LongMemEval): por padrao a busca aqui e' por
PALAVRA-CHAVE/substring, nao por embeddings/similaridade semantica --
busca semantica opcional existe (adapters/semantic_search.py) mas fica
DESLIGADA ate' AIR_ENABLE_SEMANTIC_SEARCH=true (custo de import medido
~187s nesta maquina, ver README). Em qualquer um dos dois modos, o
campo 'method' do resultado diz exatamente qual foi usado -- pra' quem
consome a tool nao presumir mais do que existe.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from core.types import Event, Fact
from memory.store import MemoryStore
from world.state import WorldState
from mcp_server.kakeya_index import KakeyaContextIndex
from adapters import semantic_search
from adapters.semantic_search import SemanticIndex

_WORD_RE = re.compile(r"[a-zA-ZÀ-ÿ0-9_]+")


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text)]


def _score(query_words: list[str], haystack: str) -> int:
    haystack_low = haystack.lower()
    return sum(1 for w in query_words if w and w in haystack_low)


def _fact_text(f: Fact) -> str:
    return f"{f.subject} {f.predicate} = {f.obj}" + (f" ({f.reason})" if f.reason else "")


def _entity_text(e) -> str:
    return f"entidade {e.kind} {e.name} {e.attrs}"


def _event_text(ev: Event) -> str:
    return f"evento {ev.kind} em {ev.entity_id} {ev.payload}"


# Texto usado SO' pra' embedding semantico (adapters/semantic_search.py),
# separado de _fact_text/_entity_text/_event_text (que continuam sendo o
# texto usado por _score() e o que aparece em SearchHit.text -- nao
# mudou). Medido, nao suposto: embedar o texto compacto orientado a
# keyword (com "subject predicate =" na frente, formato dict de attrs)
# deu similaridade PIOR contra uma parafrase real (0.32 com o wrapper
# completo, 0.31 so' tirando o "=") do que embedar so' o conteudo em
# linguagem natural (0.37, mesmo texto de _fact_text sem o prefixo
# id/predicate) -- id-like token e sintaxe de dict sao ruido pra' um
# encoder treinado em frase natural, nao sinal. Threshold de
# SEMANTIC_MATCH_THRESHOLD (0.35) foi calibrado contra ESSE formato, nao
# o compacto -- trocar um sem o outro reintroduz o problema.
def _fact_semantic_text(f: Fact) -> str:
    return f.obj + (f" ({f.reason})" if f.reason else "")


def _entity_semantic_text(e) -> str:
    attrs_text = " ".join(str(v) for v in e.attrs.values())
    return f"{e.kind} {e.name} {attrs_text}".strip()


def _event_semantic_text(ev: Event) -> str:
    payload_text = " ".join(str(v) for v in ev.payload.values())
    return f"{ev.kind} {ev.entity_id} {payload_text}".strip()


@dataclass
class SearchHit:
    kind: str            # "fact" | "entity" | "event"
    id: str
    text: str             # representacao textual legivel do item
    score: int
    metadata: dict = field(default_factory=dict)


# Cache do indice de convergencia por bissecao (mcp_server/kakeya_index.py),
# um por par (world, memory) -- normalmente um unico par por processo do
# mcp_server. Modulo global de proposito: search()/search_facts()/
# search_world() sao funcoes puras que recebem world/memory como
# argumento a cada chamada (nao guardam estado), entao o cache precisa
# viver fora delas pra' sobreviver entre chamadas -- e' exatamente o que
# torna reconstrucao rara (so' quando world.version()/memory.version()
# mudou) em vez de a cada busca.
_INDEX_CACHE: dict[tuple[int, int], KakeyaContextIndex] = {}


def _get_index(world: WorldState, memory: MemoryStore) -> KakeyaContextIndex:
    key = (id(world), id(memory))
    idx = _INDEX_CACHE.get(key)
    if idx is None:
        idx = KakeyaContextIndex()
        _INDEX_CACHE[key] = idx
    # builders sao CALLABLES, nao listas prontas -- so' executam (e so'
    # tocam o banco) se ensure_fresh() decidir que precisa reconstruir de
    # verdade (versao mudou). project=None em todo builder de proposito:
    # o indice cobre TODOS os registros, independente de projeto -- o
    # filtro por projeto continua sendo aplicado do jeito de sempre (via
    # memory.all_active(project=...)/world.all_entities(project=...)) na
    # hora de montar o conjunto "visivel" desta busca; o indice so' serve
    # pra' achar candidato mais rapido dentro do que ja' seria visivel.
    idx.ensure_fresh(
        world.version(),
        memory.version(),
        {
            "fact": lambda: [(f.id, _fact_text(f)) for f in memory.all_active(project=None)],
            "entity": lambda: [(e.id, _entity_text(e)) for e in world.all_entities(project=None)],
            "event": lambda: [(ev.id, _event_text(ev)) for ev in world.all_events(limit=500, project=None)],
        },
    )
    return idx


# Mesmo padrao de cache de _INDEX_CACHE, pro indice semantico opcional
# (adapters/semantic_search.py) -- so' e' de fato usado quando
# AIR_ENABLE_SEMANTIC_SEARCH=true (ver semantic_search.enabled());
# construir o objeto SemanticIndex aqui nao paga custo nenhum sozinho
# (o custo real e' em SemanticIndex.ensure_fresh -> embed(), so' chamado
# se habilitado).
_SEMANTIC_CACHE: dict[tuple[int, int], SemanticIndex] = {}


def _get_semantic_index(world: WorldState, memory: MemoryStore) -> SemanticIndex:
    key = (id(world), id(memory))
    idx = _SEMANTIC_CACHE.get(key)
    if idx is None:
        idx = SemanticIndex()
        _SEMANTIC_CACHE[key] = idx
    # texto SEMANTICO, nao o texto de keyword (_fact_text/etc.) -- ver
    # nota em _fact_semantic_text acima: medido, o wrapper compacto
    # orientado a keyword prejudica a similaridade contra frase natural.
    idx.ensure_fresh(
        world.version(),
        memory.version(),
        {
            "fact": lambda: [(f.id, _fact_semantic_text(f)) for f in memory.all_active(project=None)],
            "entity": lambda: [(e.id, _entity_semantic_text(e)) for e in world.all_entities(project=None)],
            "event": lambda: [(ev.id, _event_semantic_text(ev)) for ev in world.all_events(limit=500, project=None)],
        },
    )
    return idx


def _candidate_ids(index: KakeyaContextIndex, kind: str, query_words: list[str]) -> set[str] | None:
    """Uniao dos candidatos de cada palavra da busca, mais overflow_ids()
    (registros longos demais pro trecho indexado -- ver
    mcp_server/kakeya_index.py). Devolve None se nao ha' palavra de busca
    (comportamento identico a antes: _score soma zero sobre lista vazia,
    entao nao ha' candidato nenhum -- quem chama trata None como "nenhum
    resultado possivel", sem escanear nada)."""
    if not query_words:
        return None
    ids: set[str] = set()
    for w in query_words:
        if w:
            ids |= index.candidates(kind, w)
    ids |= index.overflow_ids(kind)
    return ids


def _semantic_hits_for_kind(semantic: tuple[SemanticIndex, object] | None, kind: str) -> tuple[dict[str, float], set[str]]:
    """sims: id -> similaridade (todo registro indexado dessa dimensao).
    above_threshold: so' os ids que passam de SEMANTIC_MATCH_THRESHOLD --
    esses SIM entram como candidato mesmo com zero palavra em comum (e' o
    ponto da busca semantica); sims completo fica disponivel pra' quem ja'
    e' candidato por outro motivo (keyword) tambem ganhar o score
    semantico no metadata, sem precisar passar do limiar sozinho."""
    if semantic is None:
        return {}, set()
    semantic_index, query_vector = semantic
    if query_vector is None:
        return {}, set()
    sims = semantic_index.similarity_scores(kind, query_vector)
    above = {rid for rid, s in sims.items() if s >= semantic_search.SEMANTIC_MATCH_THRESHOLD}
    return sims, above


def search_facts(memory: MemoryStore, query_words: list[str], project: str | None = None, index: KakeyaContextIndex | None = None, semantic: tuple[SemanticIndex, object] | None = None) -> list[SearchHit]:
    keyword_candidates = _candidate_ids(index, "fact", query_words) if index is not None else None
    sims, semantic_candidates = _semantic_hits_for_kind(semantic, "fact")

    if index is not None or semantic is not None:
        # o ganho real esta' aqui: busca so' os candidatos (IN (...)) em
        # vez de buscar TODO fato ativo e' descartar depois -- o indice so'
        # ajudar a decidir QUEM pontuar (o experimento original desta
        # mudanca) nao movia a agulha porque o custo dominante sempre foi
        # o fetch+construcao de objeto do SQLite, nao o _score() em si
        # (medido: benchmark_index_vs_linear() em
        # tests/test_kakeya_index.py, ver nota no README). Buscar so' os
        # candidatos corta o fetch tambem, nao so' a pontuacao. Uniao com
        # semantic_candidates: um registro sem palavra em comum mas
        # semanticamente parecido tem que entrar tambem, senao a busca
        # semantica nunca acharia nada que keyword ja' nao achasse.
        combined_ids = (keyword_candidates or set()) | semantic_candidates
        records = memory.get_facts_by_ids(combined_ids, project=project) if combined_ids else []
    else:
        # nem indice nem semantico (chamada direta, ex: teste comparando
        # contra o comportamento original) -- scan completo de sempre.
        records = memory.all_active(project=project)

    hits = []
    for f in records:
        text = _fact_text(f)
        keyword_score = _score(query_words, text)
        # so' vira float quando a busca semantica de verdade contribuiu
        # (sims nao vazio) -- com semantica desligada (caso default),
        # `score` continua int, byte-identico ao valor de antes desta
        # mudanca (2 == 2.0 e' verdade em Python, mas o tipo no dict de
        # retorno da tool nao devia mudar por uma feature que nem esta'
        # ligada).
        semantic_score = sims.get(f.id, 0.0) if sims else 0
        total = keyword_score + semantic_score
        if total > 0:
            metadata = {"subject": f.subject, "predicate": f.predicate, "obj": f.obj, "reason": f.reason, "project": f.project, "created_at": f.created_at}
            if sims:
                metadata["keyword_score"] = keyword_score
                metadata["semantic_score"] = round(semantic_score, 4)
            hits.append(SearchHit(kind="fact", id=f.id, text=text, score=total, metadata=metadata))
    return hits


def search_world(world: WorldState, query_words: list[str], project: str | None = None, index: KakeyaContextIndex | None = None, semantic: tuple[SemanticIndex, object] | None = None) -> list[SearchHit]:
    hits = []

    entity_keyword_candidates = _candidate_ids(index, "entity", query_words) if index is not None else None
    entity_sims, entity_semantic_candidates = _semantic_hits_for_kind(semantic, "entity")
    if index is not None or semantic is not None:
        combined = (entity_keyword_candidates or set()) | entity_semantic_candidates
        entities = world.get_entities_by_ids(combined, project=project) if combined else []
    else:
        entities = world.all_entities(project=project)
    for e in entities:
        text = _entity_text(e)
        keyword_score = _score(query_words, text)
        semantic_score = entity_sims.get(e.id, 0.0) if entity_sims else 0
        total = keyword_score + semantic_score
        if total > 0:
            metadata = {"name": e.name, "entity_kind": e.kind, "attrs": e.attrs, "project": e.project}
            if entity_sims:
                metadata["keyword_score"] = keyword_score
                metadata["semantic_score"] = round(semantic_score, 4)
            hits.append(SearchHit(kind="entity", id=e.id, text=text, score=total, metadata=metadata))

    event_keyword_candidates = _candidate_ids(index, "event", query_words) if index is not None else None
    event_sims, event_semantic_candidates = _semantic_hits_for_kind(semantic, "event")
    if index is not None or semantic is not None:
        combined = (event_keyword_candidates or set()) | event_semantic_candidates
        events = world.get_events_by_ids(combined, project=project) if combined else []
    else:
        events = world.all_events(limit=500, project=project)
    for ev in events:
        text = _event_text(ev)
        keyword_score = _score(query_words, text)
        semantic_score = event_sims.get(ev.id, 0.0) if event_sims else 0
        total = keyword_score + semantic_score
        if total > 0:
            metadata = {"entity_id": ev.entity_id, "event_kind": ev.kind, "payload": ev.payload, "created_at": ev.created_at}
            if event_sims:
                metadata["keyword_score"] = keyword_score
                metadata["semantic_score"] = round(semantic_score, 4)
            hits.append(SearchHit(kind="event", id=ev.id, text=text, score=total, metadata=metadata))
    return hits


def search(world: WorldState, memory: MemoryStore, query: str, limit: int = 5, project: str | None = None) -> dict:
    """Busca por palavra-chave em Memory (fatos) + World State (entidades
    e eventos). Retorna os `limit` melhores resultados por score, e o
    total de registros consultados (pra' accounting honesto -- rule 9).

    project=None (default): sem escopo, busca em tudo -- comportamento de
    antes desta mudanca. project="algo": so' considera fatos/entidades/
    eventos desse projeto MAIS os marcados como globais (project=='') --
    isola um mundo do outro sem esconder o que foi marcado de proposito
    como reutilizavel entre projetos. Eventos agora tambem sao filtrados
    (era limitacao conhecida documentada no README; world.all_events()
    ganhou o mesmo parametro project que all_entities()/memory.all_active()
    ja' tinham).

    Aceleracao por indice de convergencia por bissecao (ver
    mcp_server/kakeya_index.py -- "Kakeya" e' analogia deliberada, nao o
    problema geometrico literal): em vez de rodar _score() sobre TODO
    registro visivel, busca primeiro (via bisect, O(log n) por palavra)
    quais ids sao candidatos possiveis, e so' pontua esses -- resultado
    IDENTICO ao scan completo (mesma funcao _score, mesmo texto), so'
    mais rapido quando ha' muitos registros e poucos batem a busca.

    Busca semantica (adapters/semantic_search.py): DESLIGADA por padrao,
    so' entra em jogo com AIR_ENABLE_SEMANTIC_SEARCH=true -- e mesmo
    assim, so' se o modelo carregar com sucesso (fallback gracioso pra
    keyword puro se o pacote faltar ou o load falhar). Quando ativa,
    contribui pontuacao adicional pra registros que uma parafrase sem
    palavra em comum encontraria mas keyword sozinho nao acharia -- ver
    README "Aceleracao de busca" pro numero medido e a justificativa de
    ficar desligada por padrao (import sozinho mediu 187s nesta
    maquina)."""
    t0 = time.perf_counter()
    query_words = _tokenize(query)
    index = _get_index(world, memory)

    semantic = None
    semantic_used = False
    if semantic_search.enabled():
        query_vector_arr = semantic_search.embed([query])
        if query_vector_arr is not None:
            semantic_index = _get_semantic_index(world, memory)
            semantic = (semantic_index, query_vector_arr[0])
            semantic_used = True
        # se embed() devolveu None (pacote ausente ou load falhou),
        # `semantic` continua None -- cai pro keyword puro sem erro,
        # exatamente o fallback gracioso documentado em
        # adapters/semantic_search.py.

    fact_hits = search_facts(memory, query_words, project=project, index=index, semantic=semantic)
    world_hits = search_world(world, query_words, project=project, index=index, semantic=semantic)
    all_hits = fact_hits + world_hits
    all_hits.sort(key=lambda h: h.score, reverse=True)

    # COUNT, nao fetch inteiro -- senao o accounting sozinho pagava de
    # volta o custo que get_facts_by_ids/get_entities_by_ids/
    # get_events_by_ids acabaram de evitar (mesmo numero final de antes,
    # so' sem materializar cada linha em objeto Python pra' so' contar).
    total_considered = memory.count_active(project=project) + world.count_entities(project=project) + world.count_events(limit=500, project=project)
    top = all_hits[:limit]
    elapsed_ms = (time.perf_counter() - t0) * 1000

    method = "keyword_substring_overlap"
    if semantic_used:
        method = f"hybrid_keyword_substring_and_semantic_embedding:{semantic_search.model_name()}"

    return {
        "query": query,
        "method": method,  # honesto: diz exatamente qual combinacao gerou o resultado, nunca afirma semantica sem ser
        "project": project,
        "results": [
            {"kind": h.kind, "id": h.id, "text": h.text, "score": h.score, "metadata": h.metadata}
            for h in top
        ],
        "total_matches": len(all_hits),
        "total_records_considered": total_considered,
        "latency_ms": round(elapsed_ms, 3),
    }
