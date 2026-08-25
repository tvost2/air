"""
AIR planner -- decomposicao de objetivo em tarefas com dependencia, e
laco de execucao Reason-Act-Verify-Observe (Co-ReAct, pesquisa secao 2.5).

Achado real: nao existe planejador standalone maduro pra adaptar (so'
papers academicos e integracoes PDDL especificas de robotica) -- entao
isto e' construido de verdade, deliberadamente pequeno: nao e' um
solver de planejamento automatico (isso seria prometer mais do que da'
pra entregar num MVP), e' um executor de grafo de dependencia declarado
explicitamente, com verificacao semantica em cada passo em vez de supor
sucesso.
"""
from __future__ import annotations

from typing import Callable

from core.types import ActionResult, Goal, Task, TaskStatus, VerificationOutcome, new_id
from verification.engine import VerificationEngine


class Planner:
    def __init__(self, verification: VerificationEngine):
        self.verification = verification
        self.goals: dict[str, Goal] = {}

    def new_goal(self, description: str) -> Goal:
        g = Goal(id=new_id("goal"), description=description)
        self.goals[g.id] = g
        return g

    def add_task(self, goal: Goal, description: str, depends_on: list[str] | None = None) -> Task:
        t = Task(id=new_id("task"), goal_id=goal.id, description=description, depends_on=depends_on or [])
        goal.tasks.append(t)
        return t

    def runnable_tasks(self, goal: Goal) -> list[Task]:
        done_ids = {t.id for t in goal.tasks if t.status == TaskStatus.DONE}
        return [
            t for t in goal.tasks
            if t.status == TaskStatus.PENDING and all(dep in done_ids for dep in t.depends_on)
        ]

    def run_task(self, task: Task, action_fn: Callable[[Task], ActionResult]) -> Task:
        """Executa uma tarefa e VERIFICA o resultado antes de marcar DONE
        -- nao supoe sucesso so' porque a chamada nao lancou excecao."""
        task.status = TaskStatus.RUNNING
        result = action_fn(task)
        task.result = result

        verification = self.verification.verify(result)
        if verification.outcome == VerificationOutcome.OK:
            task.status = TaskStatus.DONE
        elif verification.outcome == VerificationOutcome.FAILED:
            task.status = TaskStatus.FAILED
        else:
            # UNKNOWN: honesto, nao finge sucesso -- fica FAILED pra forcar
            # decisao explicita (retry/replan) em vez de avancar as cegas.
            task.status = TaskStatus.FAILED
        return task

    def run_all(self, goal: Goal, action_fn: Callable[[Task], ActionResult]) -> Goal:
        while True:
            runnable = self.runnable_tasks(goal)
            if not runnable:
                break
            for task in runnable:
                self.run_task(task, action_fn)
                if task.status == TaskStatus.FAILED:
                    return goal   # para no primeiro erro -- nao finge que o resto pode continuar as cegas
        return goal
