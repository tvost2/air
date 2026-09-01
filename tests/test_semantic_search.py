"""
AIR tests -- busca semantica opcional (adapters/semantic_search.py).

AVISO: este arquivo e' LENTO de proposito e NAO roda por padrao junto com
`python tests/test_air.py`/`test_mcp_server.py`/`test_kakeya_index.py`
(mesmo padrao de benchmarks/token_benchmark.py -- separado da suite
rapida). So' importar `sentence_transformers` mediu **187 segundos**
nesta maquina (ver docstring de adapters/semantic_search.py) -- este
arquivo so' faz sentido rodar deliberadamente, sabendo do custo, nao em
todo ciclo de desenvolvimento.

Roda com `AIR_ENABLE_SEMANTIC_SEARCH=true python tests/test_semantic_search.py`
a partir de E:\\x\\air. Sem essa variavel, os testes daqui pulam (skip),
nao falham -- rodar sem querer nao trava nada.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.store import MemoryStore
from world.state import WorldState
from mcp_server import retrieval
from adapters import semantic_search

failures = []
skipped = []


def check(name: str, cond: bool):
    status = "OK" if cond else "FALHOU"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def skip(name: str, reason: str):
    print(f"[SKIP] {name} ({reason})")
    skipped.append(name)


def test_disabled_by_default_no_import_cost():
    """Sem a env var, semantic_search.enabled() e' False e nenhuma busca
    tenta carregar o modelo -- garante que o custo so' e' pago por quem
    liga de proposito."""
    was_set = "AIR_ENABLE_SEMANTIC_SEARCH" in os.environ
    saved = os.environ.pop("AIR_ENABLE_SEMANTIC_SEARCH", None)
    try:
        check("semantic_search.enabled() e' False sem a env var", semantic_search.enabled() is False)
    finally:
        if was_set and saved is not None:
            os.environ["AIR_ENABLE_SEMANTIC_SEARCH"] = saved


def test_hybrid_search_finds_paraphrase_without_shared_words():
    """O caso que motiva a feature inteira: pergunta parafraseada, ZERO
    palavra em comum com o fato armazenado -- keyword sozinho nao acha
    (prova em test_keyword_alone_misses_the_paraphrase abaixo), hibrido
    acha."""
    if not semantic_search.enabled():
        skip("hybrid: acha parafrase sem palavra em comum", "AIR_ENABLE_SEMANTIC_SEARCH nao esta' true")
        return

    memory = MemoryStore()
    world = WorldState()
    memory.remember("cache-01", "descricao", "o servico de cache roda sobre redis em producao")

    query = "qual tecnologia database usada para armazenamento rapido"
    # confere que a premissa do teste e' real antes de usar semantica:
    # zero overlap de token com _score() atual (sem essa garantia, um
    # match "achado" podia ser so' keyword coincidindo, nao prova nada
    # sobre a busca semantica em si).
    assert retrieval._score(retrieval._tokenize(query), "o servico de cache roda sobre redis em producao") == 0, \
        "premissa do teste quebrou: query passou a compartilhar palavra com o fato, escolher outro par"

    t0 = time.perf_counter()
    result = retrieval.search(world, memory, query, limit=5)
    elapsed = time.perf_counter() - t0
    print(f"[info] busca hibrida levou {elapsed:.2f}s (inclui carregar o modelo na 1a chamada)")

    check("hybrid: method reporta hibrido quando semantica contribuiu", result["method"].startswith("hybrid_keyword_substring_and_semantic_embedding:"))
    ids_found = {r["id"] for r in result["results"]}
    check("hybrid: encontrou o fato via similaridade semantica (zero overlap de palavra)", len(ids_found) >= 1 and result["total_matches"] >= 1)
    if result["results"]:
        check("hybrid: metadata inclui keyword_score e semantic_score quando semantica esta' ativa", "semantic_score" in result["results"][0]["metadata"])


def test_keyword_alone_misses_the_paraphrase():
    """Mesmo par pergunta/fato do teste acima, mas com a busca puramente
    por palavra-chave (sem indice semantico) -- tem que dar ZERO
    resultado, provando que o ganho do teste anterior e' real, nao um
    match que keyword ja' acharia sozinho."""
    memory = MemoryStore()
    world = WorldState()
    memory.remember("cache-01", "descricao", "o servico de cache roda sobre redis em producao")

    query_words = retrieval._tokenize("qual tecnologia database usada para armazenamento rapido")
    hits = retrieval.search_facts(memory, query_words, index=None)  # keyword puro, sem semantic=
    check("keyword puro: NAO acha a parafrase (prova que o overlap de palavra e' zero de verdade)", hits == [])


def test_graceful_fallback_when_disabled():
    """Com a feature desligada (comportamento default), search() nunca
    tenta tocar sentence_transformers -- resultado e' identico ao
    keyword_substring_overlap de sempre."""
    was_set = "AIR_ENABLE_SEMANTIC_SEARCH" in os.environ
    saved = os.environ.pop("AIR_ENABLE_SEMANTIC_SEARCH", None)
    try:
        memory = MemoryStore()
        world = WorldState()
        memory.remember("x", "descricao", "conteudo qualquer")
        result = retrieval.search(world, memory, "conteudo")
        check("desligado: method continua keyword_substring_overlap (nao muda por padrao)", result["method"] == "keyword_substring_overlap")
    finally:
        if was_set and saved is not None:
            os.environ["AIR_ENABLE_SEMANTIC_SEARCH"] = saved


def main():
    test_disabled_by_default_no_import_cost()
    test_keyword_alone_misses_the_paraphrase()
    test_graceful_fallback_when_disabled()
    test_hybrid_search_finds_paraphrase_without_shared_words()

    print()
    if not semantic_search.enabled():
        print("AIR_ENABLE_SEMANTIC_SEARCH nao estava true -- parte dos testes rodou como [SKIP], nao como falha.")
        print("Pra rodar de verdade: AIR_ENABLE_SEMANTIC_SEARCH=true python tests/test_semantic_search.py")
    if failures:
        print(f"{len(failures)} teste(s) falharam: {failures}")
        sys.exit(1)
    print("Todos os testes de busca semantica passaram (ou foram pulados de proposito).")


if __name__ == "__main__":
    main()
