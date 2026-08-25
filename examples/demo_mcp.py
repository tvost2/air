"""
AIR examples -- demo end-to-end do MCP server: store -> nova sessao ->
query -> MCP -> AIR -> retrieval -> contexto estruturado.

Nao inicia o processo stdio de verdade (isso exigiria um client MCP
completo do outro lado) -- chama a mesma camada de protocolo
(mcp.server.MCPServer.call_tool) que o Claude Code chamaria, o que exercita
o caminho real (schema, dispatch, serializacao), so' sem o transporte
stdio em si. E' o mesmo padrao usado em tests/test_mcp_server.py
(test_mcp_protocol_layer).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DB_PATH = Path(__file__).parent / "demo_mcp_storage.db"


def section(title: str):
    print(f"\n--- {title} ---")


async def main():
    if DB_PATH.exists():
        DB_PATH.unlink()
    os.environ["AIR_STORAGE"] = str(DB_PATH)

    # aquece o tokenizador ANTES de medir latencia -- em producao esse
    # custo (~20-30s nesta maquina, so' com o modelo em cache local) e'
    # pago uma vez na inicializacao do servidor (ver mcp_server/server.py
    # main()), nao a cada query. Sem isso aqui, a latencia do primeiro
    # air_get_context deste demo incluiria esse custo de carga e daria
    # uma impressao falsa de quanto uma query realmente custa em uso normal.
    from mcp_server import tokens as _tokens
    _tokens.count_tokens("warmup")

    section("Sessao 1 -- armazenando informacao sobre o projeto")
    from mcp_server import server as session1
    t0 = time.perf_counter()
    r = await session1.server.call_tool("air_store_memory", {
        "content": "Mudamos o Context Engine ontem pra' usar referencia por ID em vez de reenviar o conteudo inteiro -- outputs grandes agora viram handle, so' o resumo entra no prompt.",
        "metadata": {"subject": "air:context_engine", "predicate": "mudanca_recente", "reason": "reduzir tokens reenviados"},
    })
    print(json.loads(r.content[0].text))
    print(f"(latencia: {(time.perf_counter()-t0)*1000:.1f} ms)")

    section("Sessao 2 -- processo NOVO (mesmo storage), pergunta relacionada")
    # simula reinicio real: reimporta o modulo do zero, que reconstroi o
    # AirAdapter do zero (mas aponta pro mesmo arquivo AIR_STORAGE)
    import importlib
    import mcp_server.server as session2
    importlib.reload(session2)

    query = "Por que mudamos o algoritmo do Context Engine ontem?"
    print(f"Query: {query!r}")

    t0 = time.perf_counter()
    r = await session2.server.call_tool("air_search_context", {"query": query})
    search_result = json.loads(r.content[0].text)
    latency_search = (time.perf_counter() - t0) * 1000

    section("air_search_context -- resultado")
    print(json.dumps(search_result, ensure_ascii=False, indent=2))
    print(f"latencia: {latency_search:.1f} ms")

    t0 = time.perf_counter()
    r = await session2.server.call_tool("air_get_context", {"query": query})
    ctx_result = json.loads(r.content[0].text)
    latency_ctx = (time.perf_counter() - t0) * 1000

    section("air_get_context -- contexto reconstruido")
    print(f"Contexto: {ctx_result['context']}")
    print(f"Referencias: {ctx_result['references']}")
    print(f"Tokens: {ctx_result['tokens']['tokens']} (metodo: {ctx_result['tokens']['method']})")
    print(f"Registros considerados na busca: {ctx_result['total_records_considered']}")
    print(f"Etapas do planner: {[t['status'] for t in ctx_result['planner']]}")
    print(f"latencia: {latency_ctx:.1f} ms")

    section("Resumo")
    ok = len(search_result["results"]) == 1 and "referencia por ID" in ctx_result["context"]
    print(f"A informacao armazenada na sessao 1 foi corretamente recuperada na sessao 2, "
          f"sem o usuario precisar reenviar o texto original: {'SIM' if ok else 'NAO'}")
    print(f"Query length: {len(query)} chars. Contexto retornado: {len(ctx_result['context'])} chars "
          f"({ctx_result['tokens']['tokens']} tokens medidos por tokenizador real, "
          f"nao heuristica -- ver campo 'method').")

    # nao apaga o arquivo: session1/session2 mantem conexoes sqlite
    # abertas no processo, e Windows bloqueia unlink de arquivo com handle
    # aberto (mesma situacao documentada em tests/test_mcp_server.py) --
    # e' so' um arquivo de demo, fica pro proximo `rm`/nova rodada limpar.


if __name__ == "__main__":
    asyncio.run(main())
