"""
AIR examples -- demo de um agente real usando o runtime completo: World
State, Memory, Context Engine, Tools (com permissao por capacidade),
Verification, Planner. Modelo local (SmolLM2-360M-Instruct, mesmo desta
sessao) via HFLocalProvider, sem chave de API.

Cenario: agente investiga por que o servico 'api' caiu, usando fatos
estruturais do World State (nao reconstroi por conversa), memoria de
preferencia do usuario (tom de resposta), e le um arquivo de log real via
tool com permissao explicita.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.types import Capability, TaskStatus
from filesystem.fs import read_file, write_file
from models.provider import HFLocalProvider
from sdk.agent import Agent


def main():
    log_path = str(Path(__file__).parent / "demo_incident.log")
    write_file(log_path, (
        "[10:14:02] api pid=4821 nivel=INFO processando requisicao ok\n"
        "[10:14:59] api pid=4821 nivel=ERROR falha ao conectar no banco de dados apos 5 tentativas\n"
        "[10:15:00] api pid=4821 nivel=ERROR processo encerrado com codigo 1\n"
    ))

    print("Carregando modelo local (SmolLM2-360M-Instruct)...")
    agent = Agent(name="agent:demo", model=HFLocalProvider())

    # World State: fato estrutural, consultavel, nao reconstruido de conversa
    agent.world.entity("db", kind="database", id="db")
    agent.world.entity("api", kind="service", id="api")
    agent.world.relation("api", "depends_on", "db")
    agent.world.event("api", "crashed", {"exit_code": 1, "reason": "db_connection_failed"})

    # Memory: preferencia persistente do usuario, recuperada por consulta direta
    agent.remember("user:tvost", "prefers_response_tone", "direto e tecnico", reason="pedido explicito no inicio da sessao")

    # Security: permissao explicita, escopo restrito a' pasta do demo
    agent.grant(Capability.FILESYSTEM, resource=str(Path(__file__).parent / "**"))
    agent.tools.register("read_file", read_file, Capability.FILESYSTEM, resource_arg="path")

    print("\n--- Tool call: lendo log do incidente ---")
    action_result = agent.call_tool("read_file", path=log_path)
    verification = agent.verification.verify(action_result)
    print(f"Verificacao da tool: {verification.outcome.value} -- {verification.detail}")

    print("\n--- Planner: tarefas de investigacao ---")
    goal = agent.planner.new_goal("descobrir causa raiz da queda do servico api")
    t1 = agent.planner.add_task(goal, "ler log do incidente")
    t2 = agent.planner.add_task(goal, "consultar dependencias da api no world state", depends_on=[t1.id])

    def action_fn(task):
        if task.id == t1.id:
            return agent.call_tool("read_file", path=log_path)
        deps = agent.world.relations_of("api")
        from core.types import ActionResult, new_id
        return ActionResult(id=new_id("act"), tool_name="world_query", args={"entity": "api"}, output=str(deps))

    agent.planner.run_all(goal, action_fn)
    for t in goal.tasks:
        print(f"  [{t.status.value}] {t.description}")

    print("\n--- Ask: o agente responde usando SO' o que esta' no Context Engine ---")
    tone = agent.recall("user:tvost", "prefers_response_tone")
    handle = action_result.output["handle"] if isinstance(action_result.output, dict) else None
    question = f"Em tom {tone[0]}: qual foi a causa raiz da queda do servico api, segundo o log?"
    answer = agent.ask(question, extra_handles=[handle] if handle else None, max_tokens=40)
    print(f"Pergunta: {question}")
    print(f"Resposta do modelo: {answer}")

    if handle:
        item = agent.context.item(handle)
        rendered_handle_only = agent.context.render([handle])
        print(f"\nLog bruto: {item.size_chars} chars. Como entrou no prompt (so' este handle): {len(rendered_handle_only)} chars.")
        if len(rendered_handle_only) >= item.size_chars:
            print("Nota honesta: pra' um item pequeno (perto do limiar de inline), o overhead da referencia "
                  "(rotulo + resumo + instrucao de get()) pode ficar do tamanho do conteudo original ou maior -- "
                  "o ganho real do Context Engine aparece em outputs GRANDES revisitados varias vezes, nao em "
                  "um log curto lido uma vez so'. Ver benchmarks/token_benchmark.py pra' a medicao de sessao "
                  "inteira, onde o efeito e' claro.")

    ok = all(t.status == TaskStatus.DONE for t in goal.tasks) and verification.outcome.value == "ok"
    print(f"\nDemo concluido {'com sucesso' if ok else 'com pendencias -- ver status das tarefas acima'}.")


if __name__ == "__main__":
    main()
