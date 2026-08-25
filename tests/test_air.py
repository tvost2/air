"""
AIR tests -- cobre os modulos centrais (world, memory, context, security,
tools, verification, planner) via a fachada sdk/agent.py e chamadas
diretas onde faz sentido testar isolado. Roda com `python tests/test_air.py`
a partir de E:\\x\\air (sem framework externo, pra' nao adicionar
dependencia so' pra rodar teste no MVP).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from context.engine import ContextEngine, INLINE_THRESHOLD_CHARS
from core.types import ActionResult, Capability, VerificationOutcome
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
    test_memory_recency()
    test_context_engine_reference_by_id()
    test_permissions_deny_by_default()
    test_tool_registry_large_output_becomes_handle()
    test_verification_default_heuristic()
    test_planner_stops_on_failure()
    test_agent_end_to_end()

    print()
    if failures:
        print(f"{len(failures)} teste(s) falharam: {failures}")
        sys.exit(1)
    print("Todos os testes passaram.")


if __name__ == "__main__":
    main()
