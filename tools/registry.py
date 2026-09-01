"""
AIR tools -- registro e execucao de ferramenta.

Pesquisa (docs/ECOSYSTEM_RESEARCH.md secao 1): protocolo de ferramenta
em si e' MCP, padrao de fato do setor -- nao reimplementar. O que o AIR
adiciona por cima (e que nao existe pronto) e' o FIO que liga tool call a'
permissao por capacidade (security/permissions.py) e ao Context Engine:
o retorno de uma tool NUNCA volta cru pro chamador se for grande -- entra
no ContextEngine e volta so' um handle. E' aqui que o principio central
("nao reenviar informacao estruturada como token repetido") vira
mecanismo de verdade, nao so' promessa de design.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from context.engine import ContextEngine, INLINE_THRESHOLD_CHARS
from core.types import ActionResult, Capability, new_id
from security.permissions import PermissionManager


@dataclass
class ToolSpec:
    name: str
    fn: Callable[..., object]
    required_capability: Capability | None = None
    resource_arg: str | None = None   # nome do kwarg que vira o 'resource' checado na permissao (ex: "path" pra FILESYSTEM)


class ToolRegistry:
    def __init__(self, permissions: PermissionManager, context: ContextEngine):
        self._tools: dict[str, ToolSpec] = {}
        self.permissions = permissions
        self.context = context

    def register(self, name: str, fn: Callable[..., object], required_capability: Capability | None = None, resource_arg: str | None = None) -> None:
        self._tools[name] = ToolSpec(name=name, fn=fn, required_capability=required_capability, resource_arg=resource_arg)

    def call(self, principal: str, tool_name: str, **kwargs) -> ActionResult:
        spec = self._tools.get(tool_name)
        if spec is None:
            return ActionResult(id=new_id("act"), tool_name=tool_name, args=kwargs, output=None, error=f"tool desconhecida: {tool_name}")

        # Bug real, encontrado nao por inspecao mas testando o caminho que
        # nao tinha teste: permissao negada levantava PermissionDenied SEM
        # ser capturada, ao contrario de erro de tool (capturado logo
        # abaixo) -- quebrava o contrato implicito de "call() sempre
        # devolve ActionResult, nunca explode" bem no meio de
        # Planner.run_all (que dependia de action_fn NUNCA levantar pra
        # marcar a tarefa FAILED de forma graciosa) e deixava
        # Agent.call_tool sem publicar "action.finished" nesse caso
        # especifico. Capturar aqui NAO afrouxa a seguranca -- a tool
        # continua nunca sendo chamada quando falta capacidade (require()
        # ainda levanta ANTES de spec.fn rodar); so' muda como quem chama
        # fica sabendo disso, de excecao crua pra ActionResult com error
        # preenchido, igual a qualquer outra falha.
        try:
            if spec.required_capability is not None:
                resource = kwargs.get(spec.resource_arg) if spec.resource_arg else None
                self.permissions.require(principal, spec.required_capability, resource)
            raw_output = spec.fn(**kwargs)
            error = None
        except Exception as e:   # PermissionDenied inclusa -- e' subclasse de Exception
            raw_output = None
            error = str(e)

        output = raw_output
        if isinstance(raw_output, str) and len(raw_output) > INLINE_THRESHOLD_CHARS:
            handle_id = self.context.put(raw_output, kind="tool_output", label=f"resultado de {tool_name}({kwargs})")
            output = {"handle": handle_id, "preview": self.context.summarize(handle_id)}

        return ActionResult(id=new_id("act"), tool_name=tool_name, args=kwargs, output=output, error=error)
