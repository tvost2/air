"""
AIR tests -- cobre os modulos centrais (world, memory, context, security,
tools, verification, planner, events) via a fachada sdk/agent.py e
chamadas diretas onde faz sentido testar isolado. Roda com
`python tests/test_air.py` a partir de E:\\x\\air (sem framework externo,
pra' nao adicionar dependencia so' pra rodar teste no MVP).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from context.engine import ContextEngine, INLINE_THRESHOLD_CHARS
from core.types import ActionResult, Capability, VerificationOutcome
from events.bus import EventBus
from memory.store import MemoryStore
from planner.planner import Planner
from security.permissions import PermissionDenied, PermissionManager
from sdk.agent import Agent
from tools.registry import ToolRegistry
from verification.engine import VerificationEngine
from world.state import WorldState

failures = []


def check(name: str, cond: bool):
    status = "OK" if cond else "FALHOU"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def test_world_state():
    w = WorldState()
    w.entity("server-01", kind="server", id="server-01")
    w.entity("api", kind="service", id="api")
    w.relation("server-01", "depends_on", "api")
    w.event("api", "crashed", {"code": 500})

    deps = w.dependents_of("api")
    check("world.dependents_of retorna quem depende da entidade", [e.id for e in deps] == ["server-01"])

    events = w.events_of("api")
    check("world.events_of registra evento", len(events) == 1 and events[0].kind == "crashed")

    check("world.find_entity_by_name acha por nome", w.find_entity_by_name("api").id == "api")


def test_world_state_update_entity():
    w = WorldState()
    w.entity("cache-01", kind="cache", id="cache-01", attrs={"engine": "redis", "region": "us"})

    updated = w.update_entity("cache-01", attrs={"engine": "valkey"})
    check("world.update_entity: merge preserva chave nao tocada", updated.attrs["region"] == "us")
    check("world.update_entity: merge sobrescreve chave repetida", updated.attrs["engine"] == "valkey")
    check("world.update_entity: id/name preservados (mesma entidade)", updated.id == "cache-01" and updated.name == "cache-01")

    replaced = w.update_entity("cache-01", attrs={"only": "this"}, merge_attrs=False)
    check("world.update_entity: merge_attrs=False substitui attrs inteiro", replaced.attrs == {"only": "this"})

    reklinded = w.update_entity("cache-01", kind="datastore")
    check("world.update_entity: kind muda quando informado", reklinded.kind == "datastore")

    check("world.update_entity: id inexistente devolve None (nao levanta excecao)", w.update_entity("nao-existe", attrs={"x": 1}) is None)


def test_world_state_relation_and_event_project_scoping():
    w = WorldState()
    w.entity("svc-a", kind="service", id="svc-a")
    w.entity("svc-b", kind="service", id="svc-b")
    w.relation("svc-a", "depends_on", "svc-b", project="proj_x")
    w.event("svc-a", "deployed", {"v": 1}, project="proj_x")
    w.event("svc-a", "deployed", {"v": 2}, project="proj_y")

    rels = w.relations_of("svc-a")
    check("world.relation: project persiste e volta em relations_of", any(r.project == "proj_x" for r in rels))

    scoped = w.all_events(project="proj_x")
    check("world.all_events: escopado por project so' ve' o proprio + globais", len(scoped) == 1 and scoped[0].payload["v"] == 1)

    unscoped = w.all_events()
    check("world.all_events: sem project (comportamento antigo) ve tudo", len(unscoped) == 2)


def test_memory_recency():
    m = MemoryStore()
    f1 = m.remember("user:tvost", "prefers_response_tone", "casual", reason="pedido inicial")
    f2 = m.remember("user:tvost", "prefers_response_tone", "formal", reason="mudou de ideia depois")

    active = m.recall("user:tvost", "prefers_response_tone")
    check("memory.recall retorna so' o fato ATIVO mais recente", [f.obj for f in active] == ["formal"])
    check("memory novo fato aponta supersedes pro antigo", f2.supersedes == f1.id)

    hist = m.history("user:tvost", "prefers_response_tone")
    check("memory.history mostra as duas versoes", len(hist) == 2)


def test_context_engine_reference_by_id():
    ctx = ContextEngine()
    small = "ok"
    big = "x" * (INLINE_THRESHOLD_CHARS + 500)

    small_id = ctx.put(small, kind="tool_output", label="pequeno")
    big_id = ctx.put(big, kind="tool_output", label="grande")

    rendered = ctx.render([small_id, big_id])
    check("context: item pequeno entra inteiro no render", small in rendered)
    check("context: item grande NAO entra inteiro no render (so' referencia)", big not in rendered)
    check("context: item grande ainda pode ser recuperado por get()", ctx.get(big_id) == big)
    check("context: render do item grande e' bem menor que o conteudo original", len(rendered) < len(big))


def test_context_engine_delete():
    """delete() e' novo -- adicionado pra' mcp_server/adapter.py:_get_context
    poder criar um item ESPECULATIVAMENTE (pra' medir o custo real de
    renderizar, cabecalho incluido, antes de decidir se cabe no
    orcamento) e remove-lo sem deixar orfao se nao coube."""
    ctx = ContextEngine()
    handle = ctx.put("conteudo qualquer", kind="tool_output", label="teste")
    check("context.delete: remove item existente, devolve True", ctx.delete(handle) is True)
    check("context.delete: item removido nao aparece mais no render", handle not in ctx.render([handle]))
    raised = False
    try:
        ctx.get(handle)
    except KeyError:
        raised = True
    check("context.delete: get() no handle removido levanta KeyError de verdade", raised)
    check("context.delete: chamar de novo (handle ja' removido) devolve False, nao levanta excecao", ctx.delete(handle) is False)


def test_permissions_deny_by_default():
    p = PermissionManager()
    check("permissions: nega por padrao (sem grant)", p.check("agent:x", Capability.FILESYSTEM, "/etc/passwd") is False)

    p.grant("agent:x", Capability.FILESYSTEM, resource="/home/user/**")
    check("permissions: permite dentro do escopo concedido", p.check("agent:x", Capability.FILESYSTEM, "/home/user/file.txt") is True)
    check("permissions: nega fora do escopo concedido", p.check("agent:x", Capability.FILESYSTEM, "/etc/passwd") is False)

    raised = False
    try:
        p.require("agent:x", Capability.NETWORK)
    except PermissionDenied:
        raised = True
    check("permissions.require levanta PermissionDenied quando falta capacidade", raised)


def test_tool_registry_large_output_becomes_handle():
    perms = PermissionManager()
    ctx = ContextEngine()
    reg = ToolRegistry(perms, ctx)
    reg.register("big_output", lambda: "y" * (INLINE_THRESHOLD_CHARS + 100))

    result = reg.call("agent:x", "big_output")
    check("tools: output grande vira dict com handle, nao string crua", isinstance(result.output, dict) and "handle" in result.output)
    check("tools: conteudo completo ainda acessivel via context.get", len(ctx.get(result.output["handle"])) == INLINE_THRESHOLD_CHARS + 100)


def test_tool_registry_permission_denied_returns_action_result():
    """Bug real encontrado testando, nao por inspecao: permissao negada
    levantava PermissionDenied SEM ser capturada por registry.call() (so'
    erro de tool era capturado) -- quebrava o contrato de "call() sempre
    devolve ActionResult" bem no meio de Planner.run_all (que depende de
    action_fn nunca levantar pra marcar a tarefa FAILED de forma
    graciosa) e deixava Agent.call_tool sem publicar 'action.finished'
    nesse caso. Corrigido movendo permissions.require() pra dentro do
    try/except que ja existia pro erro de tool."""
    perms = PermissionManager()
    ctx = ContextEngine()
    reg = ToolRegistry(perms, ctx)
    reg.register("danger", lambda: "boom", required_capability=Capability.NETWORK)

    raised = False
    try:
        result = reg.call("agent:x", "danger")
    except PermissionDenied:
        raised = True
    check("tools: permissao negada NAO levanta excecao crua (devolve ActionResult)", raised is False)
    check("tools: ActionResult de permissao negada tem error preenchido", result.error is not None and "network" in result.error)
    check("tools: tool NUNCA foi chamada quando faltou permissao (output None, nao 'boom')", result.output is None)


def test_verification_default_heuristic():
    v = VerificationEngine()
    ok_result = ActionResult(id="a1", tool_name="x", args={}, output="algo")
    fail_result = ActionResult(id="a2", tool_name="x", args={}, output=None, error="deu ruim")
    unknown_result = ActionResult(id="a3", tool_name="x", args={}, output=None)

    check("verification: erro reportado -> FAILED", v.verify(fail_result).outcome == VerificationOutcome.FAILED)
    check("verification: sem erro e com output -> OK", v.verify(ok_result).outcome == VerificationOutcome.OK)
    check("verification: sem erro e sem output -> UNKNOWN", v.verify(unknown_result).outcome == VerificationOutcome.UNKNOWN)


def test_planner_stops_on_failure():
    v = VerificationEngine()
    pl = Planner(v)
    goal = pl.new_goal("goal de teste")
    t1 = pl.add_task(goal, "passo 1")
    t2 = pl.add_task(goal, "passo 2 depende do 1", depends_on=[t1.id])

    def action_fn(task):
        if task.id == t1.id:
            return ActionResult(id="r1", tool_name="noop", args={}, output=None, error="falhou de proposito")
        return ActionResult(id="r2", tool_name="noop", args={}, output="ok")

    pl.run_all(goal, action_fn)
    check("planner: tarefa 1 falhou", t1.status.value == "failed")
    check("planner: tarefa 2 nunca rodou (dependencia falhou antes)", t2.status.value == "pending")


def test_planner_survives_permission_denied_tool_call():
    """Prova a historia inteira, ponta a ponta: uma action_fn real que
    chama registry.call() sem ter a permissao necessaria. Antes da
    correcao em tools/registry.py, isso levantava PermissionDenied de
    dentro de action_fn e derrubava Planner.run_all inteiro (excecao nao
    tratada propagando pro chamador do teste) em vez de marcar a tarefa
    FAILED como qualquer outra falha -- exatamente o cenario que motivou
    a correcao, nao um caso hipotetico."""
    perms = PermissionManager()
    ctx = ContextEngine()
    reg = ToolRegistry(perms, ctx)
    reg.register("danger", lambda: "boom", required_capability=Capability.NETWORK)
    # perms nunca recebe grant -- a chamada tem que ser negada

    v = VerificationEngine()
    pl = Planner(v)
    goal = pl.new_goal("goal com tool sem permissao")
    t1 = pl.add_task(goal, "chamar tool sem permissao")

    def action_fn(task):
        return reg.call("agent:x", "danger")

    pl.run_all(goal, action_fn)  # nao pode levantar excecao
    check("planner: tarefa com tool sem permissao termina FAILED (nao crasha run_all)", t1.status.value == "failed")
    check("planner: resultado da tarefa carrega o motivo da negacao", t1.result is not None and "network" in (t1.result.error or ""))


def test_event_bus_publish_subscribe():
    """EventBus nao tinha teste nenhum ate' aqui, apesar de ser descrito
    no proprio docstring do modulo como o mecanismo real que Verification
    Engine/Planner usariam pra reagir a 'action.finished'/'task.failed'
    em tempo real."""
    bus = EventBus()
    received = []
    bus.subscribe("action.finished", lambda topic, payload: received.append((topic, payload)))

    bus.publish("action.finished", {"tool_name": "x"})
    check("events: handler assinado recebe o evento publicado", received == [("action.finished", {"tool_name": "x"})])

    bus.publish("outro.topico", {"y": 1})
    check("events: handler NAO recebe evento de topico diferente", len(received) == 1)


def test_event_bus_wildcard_subscriber():
    bus = EventBus()
    received_topics = []
    bus.subscribe("*", lambda topic, payload: received_topics.append(topic))

    bus.publish("a", {})
    bus.publish("b", {})
    check("events: assinante '*' recebe TODO topico publicado", received_topics == ["a", "b"])


def test_event_bus_unsubscribe():
    bus = EventBus()
    calls = []
    handler = lambda topic, payload: calls.append(topic)
    bus.subscribe("x", handler)
    bus.publish("x", {})
    bus.unsubscribe("x", handler)
    bus.publish("x", {})
    check("events: apos unsubscribe, handler nao recebe mais o evento", calls == ["x"])

    # bug real corrigido: unsubscribe() de um topico NUNCA assinado nao
    # pode levantar excecao nem criar entrada permanente no dict interno
    # (_subscribers e' defaultdict -- acessar por indice, em vez de
    # .get(), criava uma entrada vazia pra' qualquer topico passado aqui).
    raised = False
    try:
        bus.unsubscribe("nunca-assinado", handler)
    except Exception:
        raised = True
    check("events: unsubscribe de topico nunca assinado nao levanta excecao", raised is False)
    check("events: unsubscribe de topico nunca assinado nao cria entrada no dict interno", "nunca-assinado" not in bus._subscribers)


def test_agent_end_to_end():
    a = Agent()
    a.world.entity("db", kind="database", id="db")
    a.world.entity("backend", kind="service", id="backend")
    a.world.relation("backend", "depends_on", "db")
    check("agent: world state acessivel via fachada", len(a.world.dependents_of("db")) == 1)

    a.remember("user:tvost", "role", "engenheiro")
    check("agent: memory acessivel via fachada", a.recall("user:tvost", "role") == ["engenheiro"])

    a.grant(Capability.FILESYSTEM, resource="**")
    import filesystem.fs as fs
    a.tools.register("list_dir", fs.list_dir, Capability.FILESYSTEM, resource_arg="path")
    result = a.call_tool("list_dir", path=str(Path(__file__).parent))
    check("agent: tool registrada e chamada via fachada, sem erro", result.error is None)


def main():
    test_world_state()
    test_world_state_update_entity()
    test_world_state_relation_and_event_project_scoping()
    test_memory_recency()
    test_context_engine_reference_by_id()
    test_context_engine_delete()
    test_permissions_deny_by_default()
    test_tool_registry_large_output_becomes_handle()
    test_tool_registry_permission_denied_returns_action_result()
    test_verification_default_heuristic()
    test_planner_stops_on_failure()
    test_planner_survives_permission_denied_tool_call()
    test_event_bus_publish_subscribe()
    test_event_bus_wildcard_subscriber()
    test_event_bus_unsubscribe()
    test_agent_end_to_end()

    print()
    if failures:
        print(f"{len(failures)} teste(s) falharam: {failures}")
        sys.exit(1)
    print("Todos os testes passaram.")


if __name__ == "__main__":
    main()
