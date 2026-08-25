"""
AIR memory -- fatos discretos (preferencia, responsabilidade, regra),
mesmo padrao PREF/LINK/RULE validado no experimento struct-reasoning
(../struct-reasoning/memory): representacao compacta + recencia por
substituicao explicita, nao por reconstrucao de historico de conversa.

Diferenca deliberada de World State (../world/state.py): Memory guarda
"o que o usuario prefere/disse", nao "o que depende de X agora". Sao
consultas com semantica diferente, por isso ficam em modulos separados
(achado da pesquisa: confundir as duas e' o que todo projeto do mercado
faz -- Graphiti/Cognee tratam tudo como "memoria").
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core.types import Fact, FactStatus, new_id, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    obj TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    created_at REAL NOT NULL,
    supersedes TEXT
);
CREATE INDEX IF NOT EXISTS idx_facts_subject_predicate ON facts(subject, predicate);
"""


class MemoryStore:
    """API: memory.remember(subject, predicate, obj, reason=...) e
    memory.recall(subject, predicate=...) -- so' retorna fatos ACTIVE,
    resolvendo recencia automaticamente (fato novo supersede o velho na
    mesma chave subject+predicate, igual a' regra ATUAL do experimento)."""

    def __init__(self, db_path: str | Path = ":memory:"):
        # check_same_thread=False: mesma justificativa de world/state.py
        # (necessario pro mcp_server/, que despacha tool calls em worker
        # threads) -- nao afeta uso de thread unica existente.
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def remember(self, subject: str, predicate: str, obj: str, reason: str | None = None) -> Fact:
        prior = self.conn.execute(
            "SELECT id FROM facts WHERE subject = ? AND predicate = ? AND status = ?",
            (subject, predicate, FactStatus.ACTIVE.value),
        ).fetchone()
        supersedes = None
        if prior is not None:
            supersedes = prior[0]
            self.conn.execute(
                "UPDATE facts SET status = ? WHERE id = ?", (FactStatus.SUPERSEDED.value, prior[0])
            )

        f = Fact(id=new_id("fact"), subject=subject, predicate=predicate, obj=obj, reason=reason, supersedes=supersedes)
        self.conn.execute(
            "INSERT INTO facts (id, subject, predicate, obj, status, reason, created_at, supersedes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (f.id, f.subject, f.predicate, f.obj, f.status.value, f.reason, f.created_at, f.supersedes),
        )
        self.conn.commit()
        return f

    def forget(self, fact_id: str) -> None:
        self.conn.execute("UPDATE facts SET status = ? WHERE id = ?", (FactStatus.DELETED.value, fact_id))
        self.conn.commit()

    def get_fact(self, fact_id: str) -> Fact | None:
        """Busca um fato especifico por id -- faltava um jeito de
        localizar um fato sem saber subject+predicate de antemao (preciso
        pra' update/delete por id vindos de fora, ex: MCP tools)."""
        row = self.conn.execute(
            "SELECT id, subject, predicate, obj, status, reason, created_at, supersedes FROM facts WHERE id = ?",
            (fact_id,),
        ).fetchone()
        if row is None:
            return None
        return Fact(id=row[0], subject=row[1], predicate=row[2], obj=row[3], status=FactStatus(row[4]), reason=row[5], created_at=row[6], supersedes=row[7])

    def all_active(self) -> list[Fact]:
        """Todos os fatos ativos, sem filtro de subject -- usado pela
        camada de retrieval (mcp_server/retrieval.py) pra' buscar por
        palavra-chave em toda a memoria, nao so' por chave exata."""
        rows = self.conn.execute(
            "SELECT id, subject, predicate, obj, status, reason, created_at, supersedes FROM facts WHERE status = ? ORDER BY created_at",
            (FactStatus.ACTIVE.value,),
        ).fetchall()
        return [
            Fact(id=r[0], subject=r[1], predicate=r[2], obj=r[3], status=FactStatus(r[4]), reason=r[5], created_at=r[6], supersedes=r[7])
            for r in rows
        ]

    def recall(self, subject: str, predicate: str | None = None) -> list[Fact]:
        if predicate is None:
            rows = self.conn.execute(
                "SELECT id, subject, predicate, obj, status, reason, created_at, supersedes "
                "FROM facts WHERE subject = ? AND status = ? ORDER BY created_at",
                (subject, FactStatus.ACTIVE.value),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id, subject, predicate, obj, status, reason, created_at, supersedes "
                "FROM facts WHERE subject = ? AND predicate = ? AND status = ? ORDER BY created_at",
                (subject, predicate, FactStatus.ACTIVE.value),
            ).fetchall()
        return [
            Fact(id=r[0], subject=r[1], predicate=r[2], obj=r[3], status=FactStatus(r[4]), reason=r[5], created_at=r[6], supersedes=r[7])
            for r in rows
        ]

    def history(self, subject: str, predicate: str) -> list[Fact]:
        """Todas as versoes (inclusive superseded) -- auditoria/explicabilidade."""
        rows = self.conn.execute(
            "SELECT id, subject, predicate, obj, status, reason, created_at, supersedes "
            "FROM facts WHERE subject = ? AND predicate = ? ORDER BY created_at",
            (subject, predicate),
        ).fetchall()
        return [
            Fact(id=r[0], subject=r[1], predicate=r[2], obj=r[3], status=FactStatus(r[4]), reason=r[5], created_at=r[6], supersedes=r[7])
            for r in rows
        ]
