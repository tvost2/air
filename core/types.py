"""
AIR core -- tipos de dado compartilhados por todo o runtime.

Principio de design vindo direto da pesquisa de ecossistema (docs/
ECOSYSTEM_RESEARCH.md): World State e Memory sao PRIMITIVAS DE EXECUCAO
separadas -- nao e' "tudo memoria pra RAG", e' estado consultavel
("o que depende de X") + fatos discretos ("o que o usuario prefere").
Isso e' a lacuna real que a pesquisa confirmou que ninguem resolveu bem.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# World State: entidade / relacao / evento
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Entity:
    id: str
    kind: str          # ex: "server", "api", "file", "task"
    name: str
    attrs: dict = field(default_factory=dict)
    project: str = ""   # "" = global/cross-projeto (aparece em busca de qualquer projeto); senao, so' aparece pra' quem busca no mesmo projeto
    created_at: float = field(default_factory=now)


@dataclass(frozen=True)
class Relation:
    id: str
    source_id: str      # ex: "server-01"
    kind: str            # ex: "hosts", "depends_on", "owns"
    target_id: str       # ex: "api"
    project: str = ""   # mesmo mecanismo de isolamento de Entity/Fact
    created_at: float = field(default_factory=now)


@dataclass(frozen=True)
class Event:
    id: str
    entity_id: str
    kind: str            # ex: "crashed", "deployed", "updated"
    payload: dict = field(default_factory=dict)
    project: str = ""   # mesmo mecanismo de isolamento de Entity/Fact
    created_at: float = field(default_factory=now)


# ---------------------------------------------------------------------------
# Memory: fato discreto (mesmo padrao PREF/LINK/RULE ja validado no
# experimento struct-reasoning -- nao reinventa, reaproveita o que ja foi
# medido como funcionando)
# ---------------------------------------------------------------------------

class FactStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"   # substituido por um fato mais novo (recencia)
    DELETED = "deleted"


@dataclass(frozen=True)
class Fact:
    id: str
    subject: str          # ex: "user:tvost", "project:orion"
    predicate: str         # ex: "prefers_response_tone", "responsible_for"
    obj: str                # ex: "formal", "user:leonardo"
    status: FactStatus = FactStatus.ACTIVE
    reason: str | None = None    # o "porque" -- mesma disciplina do sistema de memoria real usado nesta sessao
    project: str = ""   # "" = global/cross-projeto; senao, escopo de isolamento (ver memory/store.py)
    created_at: float = field(default_factory=now)
    supersedes: str | None = None   # id do fato anterior, se houver


# ---------------------------------------------------------------------------
# Goal / Task / Plan
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Task:
    id: str
    goal_id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    depends_on: list[str] = field(default_factory=list)
    result: "ActionResult | None" = None


@dataclass
class Goal:
    id: str
    description: str
    tasks: list[Task] = field(default_factory=list)
    created_at: float = field(default_factory=now)


# ---------------------------------------------------------------------------
# Action / Result / Verification (ciclo Reason-Act-Verify-Observe, achado
# real da pesquisa: e' padrao nomeado, Co-ReAct, nao biblioteca pronta)
# ---------------------------------------------------------------------------

class VerificationOutcome(str, Enum):
    OK = "ok"
    FAILED = "failed"
    UNKNOWN = "unknown"   # nao deu pra verificar com confianca -- honesto, nao finge sucesso


@dataclass
class ActionResult:
    id: str
    tool_name: str
    args: dict
    output: object
    error: str | None = None
    started_at: float = field(default_factory=now)
    finished_at: float | None = None


@dataclass
class Verification:
    id: str
    action_result_id: str
    outcome: VerificationOutcome
    detail: str = ""
    checked_at: float = field(default_factory=now)


# ---------------------------------------------------------------------------
# Permissoes por capacidade (achado real da pesquisa: nenhum sandbox
# provider modela isso de forma reutilizavel -- E2B nem tem controle de
# egress de rede. E' peca pra construir de verdade, nao adaptar.)
# ---------------------------------------------------------------------------

class Capability(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"
    DATABASE = "database"
    FILESYSTEM = "filesystem"
    BROWSER = "browser"
