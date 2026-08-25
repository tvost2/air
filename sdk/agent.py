"""
AIR sdk -- ponto de entrada Python. Junta todas as pecas (world, memory,
context, tools, security, verification, planner, events) numa fachada
unica, pra' quem usa o AIR nao precisar instanciar cada modulo na mao.

SDK JS/TS fica pra' depois (pedido explicito do usuario: "Python SDK
primeiro"), nao existe ainda.
"""
from __future__ import annotations

from pathlib import Path

from context.engine import ContextEngine
from core.types import Capability
from events.bus import EventBus
from memory.store import MemoryStore
from models.provider import ModelProvider, EchoProvider
from planner.planner import Planner
from security.permissions import PermissionManager
from tools.registry import ToolRegistry
from verification.engine import VerificationEngine
from world.state import WorldState


class Agent:
    def __init__(
        self,
        name: str = "agent:main",
        model: ModelProvider | None = None,
        world_db: str | Path = ":memory:",
        memory_db: str | Path = ":memory:",
    ):
        self.name = name
        self.model = model or EchoProvider()

        self.world = WorldState(world_db)
        self.memory = MemoryStore(memory_db)
        self.context = ContextEngine()
        self.permissions = PermissionManager()
        self.verification = VerificationEngine()
        self.tools = ToolRegistry(self.permissions, self.context)
        self.planner = Planner(self.verification)
        self.events = EventBus()

    def grant(self, capability: Capability, resource: str | None = None) -> None:
        self.permissions.grant(self.name, capability, resource)

    def remember(self, subject: str, predicate: str, obj: str, reason: str | None = None) -> None:
        self.memory.remember(subject, predicate, obj, reason=reason)

    def recall(self, subject: str, predicate: str | None = None) -> list[str]:
        return [f.obj for f in self.memory.recall(subject, predicate)]

    def call_tool(self, tool_name: str, **kwargs):
        result = self.tools.call(self.name, tool_name, **kwargs)
        self.events.publish("action.finished", {"tool_name": tool_name, "result": result})
        return result

    def ask(self, question: str, *, extra_handles: list[str] | None = None, max_tokens: int = 512) -> str:
        """Monta o prompt usando SO' o que esta' no Context Engine (fatos
        pinned + referencias), nao reconstrucao de historico bruto --
        isto e' o principio central do AIR aplicado na pratica."""
        assembled_context = self.context.render(extra_handles)
        prompt = f"{assembled_context}\n\nPergunta: {question}\nResposta:" if assembled_context else f"Pergunta: {question}\nResposta:"
        response = self.model.complete(prompt, model="default", max_tokens=max_tokens)
        return response.text
