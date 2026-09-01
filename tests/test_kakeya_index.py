"""
AIR tests -- indice de convergencia por bissecao (mcp_server/kakeya_index.py).

Dois tipos de garantia, nao um so':
1. Diferencial: resultado com indice tem que ser BYTE-IDENTICO ao scan
   linear original (mesma _score(), so' menos registros escaneados) --
   nunca "aproximadamente igual", correcao de busca nao aceita
   aproximacao.
2. Medido, nao so' afirmado (mesma disciplina de benchmarks/
   token_benchmark.py): benchmark real comparando indice vs scan linear
   num corpus sintetico grande o bastante pra' a diferenca aparecer.

Roda com `python tests/test_kakeya_index.py` a partir de E:\\x\\air.
"""
from __future__ import annotations

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.store import MemoryStore
from world.state import WorldState
from mcp_server import retrieval

failures = []


def check(name: str, cond: bool):
    status = "OK" if cond else "FALHOU"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def _hit_key(h):
    return (h.kind, h.id, h.score)


def test_differential_small_corpus():
    """Corpus pequeno e' o caso mais facil de regredir escondido (poucos
    registros, indice quase nao ajuda em tempo mas TEM que dar o mesmo
    resultado)."""
    memory = MemoryStore()
    world = WorldState()
    memory.remember("air", "descricao", "runtime para agentes de IA")
    memory.remember("billing-api", "descricao", "servico de cobranca")
    world.entity("air", kind="project", id="ent-air")
    world.entity("billing-api", kind="service", id="ent-billing")
    world.event("ent-billing", "deployed", {"note": "bill em producao"})

    query_words = retrieval._tokenize("bill")
    idx = retrieval._get_index(world, memory)

    with_index = retrieval.search_facts(memory, query_words, index=idx)
    without_index = retrieval.search_facts(memory, query_words, index=None)
    check(
        "diferencial fact: indice acha exatamente os mesmos ids/scores que o scan linear",
        sorted(map(_hit_key, with_index)) == sorted(map(_hit_key, without_index)),
    )

    with_index_w = retrieval.search_world(world, query_words, index=idx)
    without_index_w = retrieval.search_world(world, query_words, index=None)
    check(
        "diferencial world: indice acha exatamente os mesmos ids/scores que o scan linear",
        sorted(map(_hit_key, with_index_w)) == sorted(map(_hit_key, without_index_w)),
    )
    check(
        "diferencial world: substring no meio da palavra ainda e' achada ('bill' em 'billing-api' -- semantica de substring preservada, nao virou match de palavra inteira)",
        any(h.id == "ent-billing" for h in with_index_w),
    )


def test_differential_random_corpus():
    """Corpus maior e aleatorio -- reduz a chance de um caso especifico
    coincidir por acaso entre indice e scan linear."""
    rng = random.Random(1234)
    words = ["api", "cache", "billing", "auth", "worker", "queue", "db", "gateway", "proxy", "scheduler"]
    memory = MemoryStore()
    world = WorldState()

    for i in range(300):
        obj = " ".join(rng.choices(words, k=rng.randint(2, 6))) + f" id{i}"
        memory.remember(f"svc-{i}", "descricao", obj)
    for i in range(300):
        attrs = {"tag": " ".join(rng.choices(words, k=rng.randint(1, 3)))}
        world.entity(f"ent-{i}", kind=rng.choice(words), id=f"wid-{i}", attrs=attrs)
    for i in range(300):
        payload = {"msg": " ".join(rng.choices(words, k=rng.randint(1, 4)))}
        world.event(f"wid-{i % 300}", rng.choice(words), payload)

    idx = retrieval._get_index(world, memory)
    for w in ["api", "cache", "bill", "qu", "gate", "zzz-nao-existe"]:
        query_words = [w]
        f_with = retrieval.search_facts(memory, query_words, index=idx)
        f_without = retrieval.search_facts(memory, query_words, index=None)
        check(f"diferencial fact (word='{w}'): mesmo resultado com e sem indice", sorted(map(_hit_key, f_with)) == sorted(map(_hit_key, f_without)))

        w_with = retrieval.search_world(world, query_words, index=idx)
        w_without = retrieval.search_world(world, query_words, index=None)
        check(f"diferencial world (word='{w}'): mesmo resultado com e sem indice", sorted(map(_hit_key, w_with)) == sorted(map(_hit_key, w_without)))


def test_cache_invalidates_on_write():
    memory = MemoryStore()
    world = WorldState()
    memory.remember("x", "descricao", "nada de especial aqui")
    idx1 = retrieval._get_index(world, memory)
    before = retrieval.search_facts(memory, ["fresco"], index=idx1)
    check("cache: antes de escrever, palavra nova nao acha nada", before == [])

    memory.remember("y", "descricao", "conteudo bem fresco")
    idx2 = retrieval._get_index(world, memory)  # mesmo objeto cacheado, versao mudou
    check("cache: _get_index devolve o mesmo objeto (cache por (world,memory), nao recriado)", idx1 is idx2)
    after = retrieval.search_facts(memory, ["fresco"], index=idx2)
    check("cache: depois de escrever, indice reconstroi sozinho e acha o novo fato (invalidacao por versao funciona)", len(after) == 1)


def test_overflow_records_still_found_beyond_indexed_length():
    """Registro cujo texto passa de KAKEYA_MAX_INDEXED_CHARS tem que
    continuar sendo achavel -- overflow_ids() e' a rede de seguranca
    exatamente pra' este caso, disclosed no docstring do modulo."""
    from mcp_server.kakeya_index import KAKEYA_MAX_INDEXED_CHARS

    memory = MemoryStore()
    world = WorldState()
    padding = "x" * (KAKEYA_MAX_INDEXED_CHARS + 500)
    memory.remember("longo", "descricao", padding + " marcador-raro-no-final")

    idx = retrieval._get_index(world, memory)
    hits = retrieval.search_facts(memory, ["marcador-raro-no-final"], index=idx)
    check("overflow: substring so' depois do trecho indexado ainda e' achada", len(hits) == 1 and hits[0].id.startswith("fact"))


def benchmark_index_vs_linear():
    """Nao e' afirmacao, e' medicao real -- mesmo padrao de
    benchmarks/token_benchmark.py. Corpus grande o bastante pra' a
    diferenca O(log n) vs O(n) aparecer de forma robusta, nao so' no
    limite do ruido de medicao."""
    rng = random.Random(99)
    words = ["api", "cache", "billing", "auth", "worker", "queue", "db", "gateway", "proxy", "scheduler",
             "network", "storage", "compute", "ingest", "export", "replica", "shard", "index", "token", "session"]
    memory = MemoryStore()
    world = WorldState()
    n = 4000
    for i in range(n):
        obj = " ".join(rng.choices(words, k=rng.randint(3, 8))) + f" uid{i}"
        memory.remember(f"svc-{i}", "descricao", obj)

    rare_word = "uid1"  # bate so' num punhado de registros (uid1, uid10, uid100...), nao em todos
    query_words = [rare_word]

    idx = retrieval._get_index(world, memory)  # forca construcao antes de medir (custo pago uma vez, nao a cada busca)

    t0 = time.perf_counter()
    for _ in range(20):
        with_index = retrieval.search_facts(memory, query_words, index=idx)
    t_index = (time.perf_counter() - t0) / 20

    t0 = time.perf_counter()
    for _ in range(20):
        without_index = retrieval.search_facts(memory, query_words, index=None)
    t_linear = (time.perf_counter() - t0) / 20

    check("benchmark: resultado ainda identico ao scan linear neste corpus grande", sorted(map(_hit_key, with_index)) == sorted(map(_hit_key, without_index)))
    speedup = t_linear / t_index if t_index > 0 else float("inf")
    print(f"[benchmark] n={n} registros, busca por '{rare_word}': linear={t_linear*1000:.3f}ms, indice={t_index*1000:.3f}ms, speedup={speedup:.1f}x")
    check(f"benchmark: busca com indice medida mais rapida que scan linear neste corpus (n={n})", t_index < t_linear)


def main():
    test_differential_small_corpus()
    test_differential_random_corpus()
    test_cache_invalidates_on_write()
    test_overflow_records_still_found_beyond_indexed_length()
    benchmark_index_vs_linear()

    print()
    if failures:
        print(f"{len(failures)} teste(s) falharam: {failures}")
        sys.exit(1)
    print("Todos os testes do indice Kakeya passaram.")


if __name__ == "__main__":
    main()
