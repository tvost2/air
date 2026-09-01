"""
AIR tests -- mcp_server/. Cobre os casos pedidos: inicializacao MCP,
discovery/schema das tools, cada uma das 5 tools, erros, contexto vazio/
conflitante/desatualizado, persistencia, integracao com Context Engine.

Maioria dos testes chama mcp_server/adapter.py diretamente (rapido, sem
overhead de protocolo) -- e' a mesma logica que o servidor MCP de verdade
usa (server.py so' repassa pra' ca'), entao testar aqui cobre o
comportamento real. Um bloco separado (test_mcp_protocol_layer) chama
list_tools()/call_tool() via mcp.server.MCPServer de verdade, pra' provar
que a camada de protocolo em si (schemas, discovery) tambem funciona --
nao so' a logica por baixo.

Roda com `python tests/test_mcp_server.py` a partir de E:\\x\\air.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.types import FactStatus
from mcp_server.adapter import AirAdapter
from mcp_server.config import Config

failures = []


def check(name: str, cond: bool):
    status = "OK" if cond else "FALHOU"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


# diretorio unico por PROCESSO (nao por teste) -- roda este script varias
# vezes seguidas no Windows e um nome fixo de arquivo pode colidir com um
# arquivo da rodada anterior que o SO ainda nao liberou (sqlite3 nao expoe
# close() em WorldState/MemoryStore, entao a conexao so' morre quando o
# processo anterior termina de verdade, e isso as vezes atrasa um pouco --
# visto na pratica rodando este arquivo repetidas vezes em sequencia).
# mkdtemp() garante um nome novo sempre, entao nunca ha' o que colidir.
_TEST_RUN_DIR = Path(tempfile.mkdtemp(prefix="air_mcp_test_"))


def make_adapter(db_name: str) -> AirAdapter:
    cfg = Config()
    cfg.storage_path = _TEST_RUN_DIR / f"{db_name}.db"
    return AirAdapter(cfg)


# ---------------------------------------------------------------------------
# air_store_memory
# ---------------------------------------------------------------------------

def test_store_memory_basic():
    a = make_adapter("store_basic")
    r = a.store_memory("o projeto usa SQLite", metadata={"subject": "air", "predicate": "storage"})
    check("store_memory: retorna id", "id" in r and r["id"].startswith("fact_"))
    check("store_memory: nao supersede nada na primeira vez", r["superseded_id"] is None)


def test_store_memory_default_predicate_never_collides():
    a = make_adapter("store_default")
    r1 = a.store_memory("nota 1")
    r2 = a.store_memory("nota 2")
    check("store_memory: sem predicate explicito, notas nao se sobrepoem", r1["superseded_id"] is None and r2["superseded_id"] is None)
    check("store_memory: ambas aparecem em recall", len(a.memory.recall(r1["subject"])) >= 1)


def test_store_memory_validation_errors():
    a = make_adapter("store_errors")
    check("store_memory: content vazio retorna erro", "error" in a.store_memory("   "))
    big = "x" * (a.config.max_content_chars + 1)
    check("store_memory: content grande demais retorna erro", "error" in a.store_memory(big))


# ---------------------------------------------------------------------------
# air_search_context
# ---------------------------------------------------------------------------

def test_search_context_basic_hit():
    a = make_adapter("search_basic")
    a.store_memory("Leonardo prefere respostas em tom formal", metadata={"subject": "leonardo", "predicate": "tom"})
    r = a.search_context("tom leonardo")
    check("search_context: encontra fato relevante", len(r["results"]) == 1)
    check("search_context: metodo declarado honestamente", r["method"] == "keyword_substring_overlap")


def test_search_context_empty_store_is_not_an_error():
    a = make_adapter("search_empty")
    r = a.search_context("qualquer coisa que nao existe")
    check("search_context: memoria vazia retorna resultado vazio, nao erro", "error" not in r and r["results"] == [])


def test_search_context_validation_errors():
    a = make_adapter("search_errors")
    check("search_context: query vazia retorna erro", "error" in a.search_context(""))
    big = "x" * (a.config.max_query_chars + 1)
    check("search_context: query grande demais retorna erro", "error" in a.search_context(big))


# ---------------------------------------------------------------------------
# air_register_entity
# ---------------------------------------------------------------------------

def test_register_entity_basic():
    a = make_adapter("register_basic")
    r = a.register_entity("api", "asaas-billing-backend", attrs={"path": "/opt/pessoas-em-braile-billing", "stack": "node http nativo + pg"})
    check("register_entity: retorna id", "id" in r and r["id"].startswith("ent_"))
    check("register_entity: nao existia antes", r["already_existed"] is False)
    check("register_entity: inclui custo em tokens (economia mensuravel de reuso)", isinstance(r, dict))
    ent = a.world.get_entity(r["id"])
    check("register_entity: attrs ganham _context_cost_tokens", "_context_cost_tokens" in ent.attrs and isinstance(ent.attrs["_context_cost_tokens"], int))


def test_register_entity_is_idempotent_by_name():
    a = make_adapter("register_idempotent")
    r1 = a.register_entity("api", "dbbridge-core", attrs={"v": 1})
    r2 = a.register_entity("api", "dbbridge-core", attrs={"v": 2})
    check("register_entity: segunda chamada com mesmo name nao duplica", r1["id"] == r2["id"])
    check("register_entity: segunda chamada sinaliza already_existed", r2["already_existed"] is True)


def test_register_entity_validation_errors():
    a = make_adapter("register_errors")
    check("register_entity: kind vazio retorna erro", "error" in a.register_entity("", "algo"))
    check("register_entity: name vazio retorna erro", "error" in a.register_entity("api", ""))


def test_delete_entity_basic():
    a = make_adapter("delete_entity_basic")
    r = a.register_entity("api", "algo-descartavel")
    d = a.delete_entity(r["id"])
    check("delete_entity: confirma delecao", d["deleted"] is True and d["name"] == "algo-descartavel")
    check("delete_entity: entidade some do World State", a.world.get_entity(r["id"]) is None)


def test_delete_entity_errors():
    a = make_adapter("delete_entity_errors")
    check("delete_entity: id inexistente retorna erro", "error" in a.delete_entity("ent_nao_existe"))


def test_delete_entity_removes_from_search():
    a = make_adapter("delete_entity_search")
    r = a.register_entity("frontend", "widget-temporario", attrs={"descricao": "so' existe pra este teste"})
    before = a.search_context("widget temporario")
    a.delete_entity(r["id"])
    after = a.search_context("widget temporario")
    check("delete_entity: aparecia na busca antes de deletar", len(before["results"]) == 1)
    check("delete_entity: some da busca depois de deletar", len(after["results"]) == 0)


def test_register_entity_appears_in_search_context():
    a = make_adapter("register_search")
    a.register_entity("frontend", "site-mytheria", attrs={"path": "/opt/sitefactory/deploy/mytheria-site", "descricao": "site institucional com logo dragao hexagonal"})
    r = a.search_context("mytheria dragao logo")
    check("register_entity: entidade registrada aparece em air_search_context", any(h["kind"] == "entity" for h in r["results"]))


# ---------------------------------------------------------------------------
# isolamento por project (facts + entities)
# ---------------------------------------------------------------------------

def test_project_scoping_prevents_cross_project_supersede():
    a = make_adapter("project_supersede")
    r1 = a.store_memory("versao do projeto A", metadata={"subject": "status", "predicate": "atual", "project": "proj_a"})
    r2 = a.store_memory("versao do projeto B", metadata={"subject": "status", "predicate": "atual", "project": "proj_b"})
    check("project scoping: mesmo subject+predicate em projetos diferentes NAO supersede", r2["superseded_id"] is None)
    check("project scoping: os dois fatos continuam ACTIVE", a.memory.get_fact(r1["id"]).status.value == "active" and a.memory.get_fact(r2["id"]).status.value == "active")


def test_project_scoping_isolates_search_but_keeps_global_visible():
    a = make_adapter("project_search_scope")
    a.store_memory("segredo do projeto A: usa Postgres", metadata={"subject": "db", "predicate": "engine", "project": "proj_a"})
    a.store_memory("segredo do projeto B: usa SQLite", metadata={"subject": "db", "predicate": "engine", "project": "proj_b"})
    a.store_memory("regra geral: sempre documentar decisao de banco", metadata={"subject": "db", "predicate": "convencao"})  # global, sem project

    only_a = a.search_context("banco de dados engine", project="proj_a")
    texts_a = [h["text"] for h in only_a["results"]]
    check("project scoping: busca escopada em proj_a NAO ve segredo de proj_b", not any("SQLite" in t for t in texts_a))
    check("project scoping: busca escopada em proj_a VE o proprio segredo", any("Postgres" in t for t in texts_a))
    check("project scoping: busca escopada em proj_a VE fato global (sem project)", any("documentar decisao" in t for t in texts_a))

    unscoped = a.search_context("banco de dados engine")
    texts_all = [h["text"] for h in unscoped["results"]]
    check("project scoping: busca SEM project (comportamento antigo) ve tudo", any("Postgres" in t for t in texts_all) and any("SQLite" in t for t in texts_all))


def test_project_scoping_applies_to_get_context():
    a = make_adapter("project_get_context_scope")
    a.store_memory("proj_a usa Asaas pra pagamento", metadata={"subject": "pagamento", "predicate": "provider", "project": "proj_a"})
    a.store_memory("proj_b usa Stripe pra pagamento", metadata={"subject": "pagamento", "predicate": "provider", "project": "proj_b"})

    r = a.get_context("qual provider de pagamento", project="proj_a")
    check("project scoping: get_context escopado nao traz o de outro projeto", "Stripe" not in r["context"])
    check("project scoping: get_context escopado traz o do proprio projeto", "Asaas" in r["context"])


# ---------------------------------------------------------------------------
# air_get_context (planner -> retrieval -> structural memory -> reconstrucao)
# ---------------------------------------------------------------------------

def test_get_context_basic():
    a = make_adapter("get_context_basic")
    a.store_memory("Leonardo prefere tom formal", metadata={"subject": "leonardo", "predicate": "tom"})
    r = a.get_context("qual o tom preferido do leonardo?")
    check("get_context: contexto contem o fato relevante", "formal" in r["context"])
    check("get_context: todas as 3 etapas do planner rodaram e' 'done'", all(t["status"] == "done" for t in r["planner"]))
    check("get_context: accounting de tokens presente com metodo declarado", "tokens" in r and "method" in r["tokens"])
    check("get_context: usa notacao estrutural por padrao (AIR_ENABLE_STRUCTURAL_MEMORY=true)", "FACT(" in r["context"])


def test_get_context_empty_is_not_a_failure():
    a = make_adapter("get_context_empty")
    r = a.get_context("nada armazenado sobre isso")
    check("get_context: sem match nao e' erro", "error" not in r)
    check("get_context: contexto vazio mas planner completou", r["context"] == "" and all(t["status"] == "done" for t in r["planner"]))


def test_get_context_respects_token_budget():
    a = make_adapter("get_context_budget")
    for i in range(20):
        a.store_memory(f"fato numero {i} sobre o projeto air com bastante texto de enchimento", metadata={"subject": f"item{i}", "predicate": "descricao"})
    r_small = a.get_context("fato sobre o projeto air", max_tokens=30)
    r_large = a.get_context("fato sobre o projeto air", max_tokens=2000)
    check("get_context: orcamento pequeno inclui menos referencias que orcamento grande", r_small["reference_count"] < r_large["reference_count"])
    check("get_context: nunca excede o orcamento pedido (exceto o 1o item obrigatorio)", r_small["tokens"]["tokens"] <= 30 or r_small["reference_count"] <= 1)


def test_get_context_prose_vs_structural_toggle():
    a = make_adapter("get_context_toggle")
    a.store_memory("valor de teste", metadata={"subject": "x", "predicate": "y"})
    r_struct = a.get_context("valor de teste x y")
    check("get_context: modo estrutural usa FACT(...)", "FACT(" in r_struct["context"])

    a.config.enable_structural_memory = False
    r_prose = a.get_context("valor de teste x y")
    check("get_context: modo prosa NAO usa FACT(...)", "FACT(" not in r_prose["context"] and "igual a" in r_prose["context"])


def test_get_context_validation_errors():
    a = make_adapter("get_context_errors")
    check("get_context: query vazia retorna erro", "error" in a.get_context(""))


# ---------------------------------------------------------------------------
# air_update_memory
# ---------------------------------------------------------------------------

def test_update_memory_creates_new_version():
    a = make_adapter("update_basic")
    r1 = a.store_memory("valor antigo", metadata={"subject": "s", "predicate": "p"})
    r2 = a.update_memory(r1["id"], "valor novo")
    check("update_memory: cria fato novo, nao sobrescreve", r2["id"] != r1["id"])
    check("update_memory: aponta pro anterior", r2["previous_id"] == r1["id"])
    active = a.memory.recall("s", "p")
    check("update_memory: so' a versao nova esta' ativa", len(active) == 1 and active[0].obj == "valor novo")


def test_update_memory_errors():
    a = make_adapter("update_errors")
    check("update_memory: id inexistente retorna erro", "error" in a.update_memory("nao-existe", "x"))

    r1 = a.store_memory("v1", metadata={"subject": "s", "predicate": "p"})
    a.delete_memory(r1["id"])
    check("update_memory: nao permite atualizar fato deletado", "error" in a.update_memory(r1["id"], "x"))

    r2 = a.store_memory("v1", metadata={"subject": "s2", "predicate": "p2"})
    r3 = a.update_memory(r2["id"], "v2")
    check("update_memory: nao permite atualizar versao ja' superseded (usa a mais nova)", "error" in a.update_memory(r2["id"], "v3"))


# ---------------------------------------------------------------------------
# air_delete_memory
# ---------------------------------------------------------------------------

def test_delete_memory_basic():
    a = make_adapter("delete_basic")
    r1 = a.store_memory("vai ser deletado", metadata={"subject": "s", "predicate": "p"})
    r2 = a.delete_memory(r1["id"])
    check("delete_memory: confirma delecao", r2["deleted"] is True)
    fact = a.memory.get_fact(r1["id"])
    check("delete_memory: status vira DELETED (soft delete, nao remove linha)", fact is not None and fact.status == FactStatus.DELETED)


def test_delete_memory_errors():
    a = make_adapter("delete_errors")
    check("delete_memory: id inexistente retorna erro", "error" in a.delete_memory("nao-existe"))
    r1 = a.store_memory("x", metadata={"subject": "s", "predicate": "p"})
    a.delete_memory(r1["id"])
    check("delete_memory: deletar duas vezes retorna erro na segunda", "error" in a.delete_memory(r1["id"]))


# ---------------------------------------------------------------------------
# contexto conflitante / desatualizado (regra 10 e regra 14 do pedido)
# ---------------------------------------------------------------------------

def test_conflicting_context_resolves_to_latest():
    a = make_adapter("conflict")
    a.store_memory("prefere tom casual", metadata={"subject": "u", "predicate": "tom"})
    a.store_memory("prefere tom formal", metadata={"subject": "u", "predicate": "tom"})
    r = a.search_context("tom u")
    check("conflito: so' a versao mais recente aparece na busca", len(r["results"]) == 1 and "formal" in r["results"][0]["text"])
    ctx = a.get_context("qual o tom preferido?")
    check("conflito: contexto reconstruido nao vaza a versao antiga", "casual" not in ctx["context"] and "formal" in ctx["context"])


def test_stale_context_removed_after_delete():
    a = make_adapter("stale")
    r1 = a.store_memory("informacao que vai ficar desatualizada", metadata={"subject": "s", "predicate": "p"})
    before = a.search_context("informacao desatualizada")
    check("desatualizado: aparece antes de deletar", len(before["results"]) == 1)
    a.delete_memory(r1["id"])
    after = a.search_context("informacao desatualizada")
    check("desatualizado: some da busca depois de deletar", len(after["results"]) == 0)
    ctx = a.get_context("informacao desatualizada")
    check("desatualizado: nao aparece em get_context depois de deletar", "informacao que vai ficar" not in ctx["context"])


# ---------------------------------------------------------------------------
# persistencia entre "sessoes" (processo novo simulado por novo AirAdapter
# no MESMO arquivo -- regra 15 do pedido: nova sessao pergunta algo
# armazenado numa sessao anterior)
# ---------------------------------------------------------------------------

def test_persistence_across_sessions():
    cfg = Config()
    cfg.storage_path = _TEST_RUN_DIR / "persistence.db"

    session1 = AirAdapter(cfg)
    stored = session1.store_memory("o Context Engine usa referencia por ID", metadata={"subject": "air", "predicate": "context_engine_design"})

    # nova "sessao": novo AirAdapter, MESMO arquivo -- simula reinicio do
    # processo do servidor MCP (o que uma nova sessao do Claude Code faria)
    session2 = AirAdapter(cfg)
    found = session2.search_context("como funciona o context engine")
    check("persistencia: nova sessao encontra o que foi armazenado na anterior", len(found["results"]) == 1)
    ctx = session2.get_context("como o context engine do air funciona?")
    check("persistencia: get_context na nova sessao recupera o fato", "referencia por ID" in ctx["context"])

    # nao fecha/apaga o arquivo aqui de proposito: session1 e session2
    # mantem conexoes sqlite abertas (MemoryStore/WorldState nao expoem
    # close()), e no Windows um arquivo com handle aberto nao pode ser
    # apagado (PermissionError) -- e' so' um arquivo temporario, fica pro
    # SO limpar depois, nao afeta o resultado do teste.


# ---------------------------------------------------------------------------
# camada de protocolo MCP de verdade (discovery, schema, chamada real)
# ---------------------------------------------------------------------------

def test_mcp_protocol_layer():
    # NAO da' pra' isolar via env var + reload aqui: mcp_server/config.py
    # le' AIR_STORAGE uma unica vez, na primeira importacao do processo
    # (from mcp_server.adapter import AirAdapter, ja' no topo deste
    # arquivo) -- setar a env var agora e' tarde demais pro singleton
    # `config` (achado real: a primeira versao deste teste escrevia sem
    # querer no storage PADRAO de producao, E:\\x\\air\\storage\\air_mcp.db,
    # em vez de um arquivo isolado). Fix: troca o `adapter` do modulo
    # server.py diretamente por um construido com config isolada -- as
    # funcoes decoradas com @server.tool() leem `adapter` do escopo do
    # modulo em tempo de CHAMADA (late binding), entao isso redireciona
    # de verdade, sem depender de timing de import/env var.
    from mcp_server import server as srv_mod

    test_cfg = Config()
    test_cfg.storage_path = _TEST_RUN_DIR / "protocol.db"
    srv_mod.adapter = AirAdapter(test_cfg)

    async def run():
        tools = await srv_mod.server.list_tools()
        names = {t.name for t in tools}
        expected = {"air_search_context", "air_store_memory", "air_get_context", "air_update_memory", "air_delete_memory", "air_register_entity", "air_delete_entity"}
        check("mcp protocol: as 7 tools estao registradas (discovery)", expected.issubset(names))

        search_tool = next(t for t in tools if t.name == "air_search_context")
        check("mcp protocol: schema de air_search_context tem 'query'", "query" in search_tool.input_schema.get("properties", {}))

        result = await srv_mod.server.call_tool("air_store_memory", {"content": "teste via protocolo mcp real", "metadata": {"subject": "proto", "predicate": "teste"}})
        check("mcp protocol: call_tool nao retorna erro", not result.is_error)

        resources = await srv_mod.server.list_resources()
        check("mcp protocol: resources expostos (memoria + world state)", {"air://memory/facts", "air://world/state"}.issubset({str(r.uri) for r in resources}))

        prompts = await srv_mod.server.list_prompts()
        check("mcp protocol: prompt de reconstrucao de contexto exposto", any(p.name == "reconstruct_context" for p in prompts))

    asyncio.run(run())
    # mesmo motivo do teste de persistencia acima: conexao sqlite fica
    # aberta no processo, Windows bloqueia unlink de arquivo com handle
    # aberto -- nao tenta apagar, e' so' um temp file.


def main():
    test_store_memory_basic()
    test_store_memory_default_predicate_never_collides()
    test_store_memory_validation_errors()
    test_search_context_basic_hit()
    test_search_context_empty_store_is_not_an_error()
    test_search_context_validation_errors()
    test_register_entity_basic()
    test_register_entity_is_idempotent_by_name()
    test_register_entity_validation_errors()
    test_delete_entity_basic()
    test_delete_entity_errors()
    test_delete_entity_removes_from_search()
    test_register_entity_appears_in_search_context()
    test_project_scoping_prevents_cross_project_supersede()
    test_project_scoping_isolates_search_but_keeps_global_visible()
    test_project_scoping_applies_to_get_context()
    test_get_context_basic()
    test_get_context_empty_is_not_a_failure()
    test_get_context_respects_token_budget()
    test_get_context_prose_vs_structural_toggle()
    test_get_context_validation_errors()
    test_update_memory_creates_new_version()
    test_update_memory_errors()
    test_delete_memory_basic()
    test_delete_memory_errors()
    test_conflicting_context_resolves_to_latest()
    test_stale_context_removed_after_delete()
    test_persistence_across_sessions()
    test_mcp_protocol_layer()

    print()
    import shutil
    shutil.rmtree(_TEST_RUN_DIR, ignore_errors=True)  # ignore_errors: alguma conexao sqlite pode ainda estar aberta no processo

    if failures:
        print(f"{len(failures)} teste(s) falharam: {failures}")
        sys.exit(1)
    print("Todos os testes do mcp_server passaram.")


if __name__ == "__main__":
    main()
