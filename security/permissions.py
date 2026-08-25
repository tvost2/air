"""
AIR security -- permissao granular composta por capacidade.

Achado real da pesquisa (docs/ECOSYSTEM_RESEARCH.md secao 2.4): nenhum
provedor de sandbox (E2B, Modal, Daytona) modela isso de forma
reutilizavel; E2B nem tem controle de egress de rede. Isolamento de
processo esta' resolvido (Firecracker/gVisor, adaptar via adapters/), mas
"quem pode fazer o que, em qual escopo" nao esta' -- entao e' construido
aqui de verdade.

Modelo: um Grant concede uma Capability a um principal (agente/tool/
sessao), opcionalmente restrita a um padrao de recurso (path glob pra
FILESYSTEM, host pra NETWORK, etc). check() nega por padrao -- allowlist,
nao denylist, o oposto do que causa vazamento de escopo em produção.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

from core.types import Capability, new_id, now


@dataclass(frozen=True)
class Grant:
    id: str
    principal: str          # ex: "agent:main", "tool:shell_exec"
    capability: Capability
    resource: str | None = None   # ex: "/home/user/project/**", "api.example.com" -- None = qualquer recurso dessa capacidade
    created_at: float = field(default_factory=now)


class PermissionDenied(Exception):
    pass


class PermissionManager:
    def __init__(self):
        self._grants: list[Grant] = []

    def grant(self, principal: str, capability: Capability, resource: str | None = None) -> Grant:
        g = Grant(id=new_id("grant"), principal=principal, capability=capability, resource=resource)
        self._grants.append(g)
        return g

    def revoke(self, grant_id: str) -> None:
        self._grants = [g for g in self._grants if g.id != grant_id]

    def check(self, principal: str, capability: Capability, resource: str | None = None) -> bool:
        for g in self._grants:
            if g.principal != principal or g.capability != capability:
                continue
            if g.resource is None:
                return True
            if resource is not None and fnmatch.fnmatch(resource, g.resource):
                return True
        return False

    def require(self, principal: str, capability: Capability, resource: str | None = None) -> None:
        if not self.check(principal, capability, resource):
            scope = f" em '{resource}'" if resource else ""
            raise PermissionDenied(f"'{principal}' nao tem '{capability.value}'{scope}")

    def grants_for(self, principal: str) -> list[Grant]:
        return [g for g in self._grants if g.principal == principal]
