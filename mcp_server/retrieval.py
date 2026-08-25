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

_WORD_RE = re.compile(r"[a-zA-ZÀ-ÿ0-9_]+")


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text)]


def _score(query_words: list[str], haystack: str) -> int:
    haystack_low = haystack.lower()
    return sum(1 for w in query_words if w and w in haystack_low)


@dataclass
class SearchHit:
    kind: str            # "fact" | "entity" | "event"
    id: str
    text: str             # representacao textual legivel do item
    score: int
    metadata: dict = field(default_factory=dict)


def search_facts(memory: MemoryStore, query_words: list[str]) -> list[SearchHit]:
    hits = []
    for f in memory.all_active():
        text = f"{f.subject} {f.predicate} = {f.obj}" + (f" ({f.reason})" if f.reason else "")
        score = _score(query_words, text)
        if score > 0:
            hits.append(SearchHit(
                kind="fact", id=f.id, text=text, score=score,
                metadata={"subject": f.subject, "predicate": f.predicate, "obj": f.obj, "reason": f.reason, "created_at": f.created_at},
            ))
    return hits


def search_world(world: WorldState, query_words: list[str]) -> list[SearchHit]:
    hits = []
    for e in world.all_entities():
        text = f"entidade {e.kind} {e.name} {e.attrs}"
        score = _score(query_words, text)
        if score > 0:
            hits.append(SearchHit(kind="entity", id=e.id, text=text, score=score, metadata={"name": e.name, "entity_kind": e.kind, "attrs": e.attrs}))

    for ev in world.all_events(limit=500):
        text = f"evento {ev.kind} em {ev.entity_id} {ev.payload}"
        score = _score(query_words, text)
        if score > 0:
            hits.append(SearchHit(kind="event", id=ev.id, text=text, score=score, metadata={"entity_id": ev.entity_id, "event_kind": ev.kind, "payload": ev.payload, "created_at": ev.created_at}))
    return hits


def search(world: WorldState, memory: MemoryStore, query: str, limit: int = 5) -> dict:
    """Busca por palavra-chave em Memory (fatos) + World State (entidades
    e eventos). Retorna os `limit` melhores resultados por score, e o
    total de registros consultados (pra' accounting honesto -- rule 9)."""
    t0 = time.perf_counter()
    query_words = _tokenize(query)

    fact_hits = search_facts(memory, query_words)
    world_hits = search_world(world, query_words)
    all_hits = fact_hits + world_hits
    all_hits.sort(key=lambda h: h.score, reverse=True)

    total_considered = len(memory.all_active()) + len(world.all_entities()) + len(world.all_events(limit=500))
    top = all_hits[:limit]
    elapsed_ms = (time.perf_counter() - t0) * 1000

    return {
        "query": query,
        "method": "keyword_substring_overlap",  # honesto: nao e' busca semantica/embeddings
        "results": [
            {"kind": h.kind, "id": h.id, "text": h.text, "score": h.score, "metadata": h.metadata}
            for h in top
        ],
        "total_matches": len(all_hits),
        "total_records_considered": total_considered,
        "latency_ms": round(elapsed_ms, 3),
    }
