"""
AIR events -- barramento de notificacao em processo (pub/sub), DIFERENTE
do log persistente de eventos por entidade que mora em world/state.py.

Por que os dois existem separados: world.event(...) e' fato historico
durravel ("api crashed as 14:32") que fica no SQLite pra consulta depois.
EventBus e' mecanismo efemero de notificacao pra outras partes do runtime
reagirem em tempo real (ex: Verification Engine assina "action.finished"
pra decidir sucesso; Planner assina "task.failed" pra replanejar). Um e'
armazenamento, o outro e' coordenacao -- confundir os dois e' exatamente
o que os frameworks de "memoria geral" pesquisados fazem.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class EventBus:
    _subscribers: dict[str, list[Callable[[str, dict], None]]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def subscribe(self, topic: str, handler: Callable[[str, dict], None]) -> None:
        self._subscribers[topic].append(handler)

    def unsubscribe(self, topic: str, handler: Callable[[str, dict], None]) -> None:
        if handler in self._subscribers[topic]:
            self._subscribers[topic].remove(handler)

    def publish(self, topic: str, payload: dict) -> None:
        for handler in list(self._subscribers.get(topic, [])):
            handler(topic, payload)
        for handler in list(self._subscribers.get("*", [])):
            handler(topic, payload)
