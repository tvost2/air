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
struct-reasoning/LongMemEval): a busca aqui e' por PALAVRA-CHAVE/
substring, nao por embeddings/similaridade semantica. Isso e' dito
explicitamente em todo resultado devolvido (campo 'method'), pra' quem
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


def search_facts(memory: MemoryStore, query_words: list[str], project: str | None = None, index: KakeyaContextIndex | None = None) -> list[SearchHit]:
    if index is not None:
        candidate_ids = _candidate_ids(index, "fact", query_words)
        if candidate_ids is None:
            return []  # sem palavra de busca -- nada bate, nem vale tocar o banco
        # o ganho real esta' aqui: busca so' os candidatos (IN (...)) em
        # vez de buscar TODO fato ativo e' descartar depois -- o indice so'
        # ajudar a decidir QUEM pontuar (o experimento original desta
        # mudanca) nao movia a agulha porque o custo dominante sempre foi
        # o fetch+construcao de objeto do SQLite, nao o _score() em si
        # (medido: benchmark_index_vs_linear() em
        # tests/test_kakeya_index.py, ver nota no README). Buscar so' os
        # candidatos corta o fetch tambem, nao so' a pontuacao.
        records = memory.get_facts_by_ids(candidate_ids, project=project)
    else:
        # sem indice (chamada direta, ex: teste comparando contra o
        # comportamento original) -- scan completo de sempre.
        records = memory.all_active(project=project)

    hits = []
    for f in records:
        text = _fact_text(f)
        score = _score(query_words, text)
        if score > 0:
            hits.append(SearchHit(
                kind="fact", id=f.id, text=text, score=score,
                metadata={"subject": f.subject, "predicate": f.predicate, "obj": f.obj, "reason": f.reason, "project": f.project, "created_at": f.created_at},
            ))
    return hits


def search_world(world: WorldState, query_words: list[str], project: str | None = None, index: KakeyaContextIndex | None = None) -> list[SearchHit]:
    hits = []

    if index is not None:
        entity_candidates = _candidate_ids(index, "entity", query_words)
        entities = world.get_entities_by_ids(entity_candidates, project=project) if entity_candidates else []
    else:
        entities = world.all_entities(project=project)
    for e in entities:
        text = _entity_text(e)
        score = _score(query_words, text)
        if score > 0:
            hits.append(SearchHit(kind="entity", id=e.id, text=text, score=score, metadata={"name": e.name, "entity_kind": e.kind, "attrs": e.attrs, "project": e.project}))

    if index is not None:
        event_candidates = _candidate_ids(index, "event", query_words)
        events = world.get_events_by_ids(event_candidates, project=project) if event_candidates else []
    else:
        events = world.all_events(limit=500, project=project)
    for ev in events:
        text = _event_text(ev)
        score = _score(query_words, text)
        if score > 0:
            hits.append(SearchHit(kind="event", id=ev.id, text=text, score=score, metadata={"entity_id": ev.entity_id, "event_kind": ev.kind, "payload": ev.payload, "created_at": ev.created_at}))
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
    mais rapido quando ha' muitos registros e poucos batem a busca."""
    t0 = time.perf_counter()
    query_words = _tokenize(query)
    index = _get_index(world, memory)

    fact_hits = search_facts(memory, query_words, project=project, index=index)
    world_hits = search_world(world, query_words, project=project, index=index)
    all_hits = fact_hits + world_hits
    all_hits.sort(key=lambda h: h.score, reverse=True)

    # COUNT, nao fetch inteiro -- senao o accounting sozinho pagava de
    # volta o custo que get_facts_by_ids/get_entities_by_ids/
    # get_events_by_ids acabaram de evitar (mesmo numero final de antes,
    # so' sem materializar cada linha em objeto Python pra' so' contar).
    total_considered = memory.count_active(project=project) + world.count_entities(project=project) + world.count_events(limit=500, project=project)
    top = all_hits[:limit]
    elapsed_ms = (time.perf_counter() - t0) * 1000

    return {
        "query": query,
        "method": "keyword_substring_overlap",  # honesto: nao e' busca semantica/embeddings
        "project": project,
        "results": [
            {"kind": h.kind, "id": h.id, "text": h.text, "score": h.score, "metadata": h.metadata}
            for h in top
        ],
        "total_matches": len(all_hits),
        "total_records_considered": total_considered,
        "latency_ms": round(elapsed_ms, 3),
    }
