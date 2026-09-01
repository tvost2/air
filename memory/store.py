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
    project TEXT NOT NULL DEFAULT '',
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
        self._migrate_add_project_column()
        self.conn.commit()

    def _migrate_add_project_column(self) -> None:
        # mesmo raciocinio de world/state.py: coluna nova, banco existente
        # (ex: storage compartilhado de producao) nao ganha ela so' com
        # CREATE TABLE IF NOT EXISTS. Idempotente -- so' roda se faltar.
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(facts)").fetchall()}
        if "project" not in cols:
            self.conn.execute("ALTER TABLE facts ADD COLUMN project TEXT NOT NULL DEFAULT ''")

    def remember(self, subject: str, predicate: str, obj: str, reason: str | None = None, project: str = "") -> Fact:
        # supersede tem que ser filtrado por project tambem -- senao dois
        # projetos diferentes usando o mesmo subject+predicate por
        # coincidencia apagariam o fato um do outro silenciosamente (era
        # exatamente esse o bug de isolamento: nada aqui sabia "de qual
        # mundo" o fato antigo era).
        prior = self.conn.execute(
            "SELECT id FROM facts WHERE subject = ? AND predicate = ? AND status = ? AND project = ?",
            (subject, predicate, FactStatus.ACTIVE.value, project),
        ).fetchone()
        supersedes = None
        if prior is not None:
            supersedes = prior[0]
            self.conn.execute(
                "UPDATE facts SET status = ? WHERE id = ?", (FactStatus.SUPERSEDED.value, prior[0])
            )

        f = Fact(id=new_id("fact"), subject=subject, predicate=predicate, obj=obj, reason=reason, project=project, supersedes=supersedes)
        self.conn.execute(
            "INSERT INTO facts (id, subject, predicate, obj, status, reason, project, created_at, supersedes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f.id, f.subject, f.predicate, f.obj, f.status.value, f.reason, f.project, f.created_at, f.supersedes),
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
            "SELECT id, subject, predicate, obj, status, reason, project, created_at, supersedes FROM facts WHERE id = ?",
            (fact_id,),
        ).fetchone()
        if row is None:
            return None
        return Fact(id=row[0], subject=row[1], predicate=row[2], obj=row[3], status=FactStatus(row[4]), reason=row[5], project=row[6], created_at=row[7], supersedes=row[8])

    def all_active(self, project: str | None = None) -> list[Fact]:
        """Todos os fatos ativos -- usado pela camada de retrieval
        (mcp_server/retrieval.py) pra' buscar por palavra-chave em toda a
        memoria, nao so' por chave exata.

        project=None (default): sem filtro -- comportamento de antes desta
        mudanca. project="algo": so' fatos desse projeto MAIS os globais
        (project==''), mesma regra de world.all_entities()."""
        if project is None:
            rows = self.conn.execute(
                "SELECT id, subject, predicate, obj, status, reason, project, created_at, supersedes FROM facts WHERE status = ? ORDER BY created_at",
                (FactStatus.ACTIVE.value,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id, subject, predicate, obj, status, reason, project, created_at, supersedes "
                "FROM facts WHERE status = ? AND (project = ? OR project = '') ORDER BY created_at",
                (FactStatus.ACTIVE.value, project),
            ).fetchall()
        return [
            Fact(id=r[0], subject=r[1], predicate=r[2], obj=r[3], status=FactStatus(r[4]), reason=r[5], project=r[6], created_at=r[7], supersedes=r[8])
            for r in rows
        ]

    def recall(self, subject: str, predicate: str | None = None, project: str | None = None) -> list[Fact]:
        # project=None: comportamento antigo, ignora escopo (chave exata
        # subject+predicate ja' e' especifica o bastante pro uso original
        # deste metodo). project="": so' fatos globais com essa chave.
        # project="algo": so' fatos desse projeto (recall e' lookup direto
        # por chave conhecida, nao busca -- aqui faz sentido ser estrito,
        # sem OR com global, diferente de all_active/all_entities).
        clauses = ["status = ?"]
        params: list = [FactStatus.ACTIVE.value]
        if project is not None:
            clauses.append("project = ?")
            params.append(project)
        clauses.append("subject = ?")
        params.append(subject)
        if predicate is not None:
            clauses.append("predicate = ?")
            params.append(predicate)
        where = " AND ".join(clauses)
        rows = self.conn.execute(
            f"SELECT id, subject, predicate, obj, status, reason, project, created_at, supersedes "
            f"FROM facts WHERE {where} ORDER BY created_at",
            params,
        ).fetchall()
        return [
            Fact(id=r[0], subject=r[1], predicate=r[2], obj=r[3], status=FactStatus(r[4]), reason=r[5], project=r[6], created_at=r[7], supersedes=r[8])
            for r in rows
        ]

    def history(self, subject: str, predicate: str) -> list[Fact]:
        """Todas as versoes (inclusive superseded) -- auditoria/explicabilidade."""
        rows = self.conn.execute(
            "SELECT id, subject, predicate, obj, status, reason, project, created_at, supersedes "
            "FROM facts WHERE subject = ? AND predicate = ? ORDER BY created_at",
            (subject, predicate),
        ).fetchall()
        return [
            Fact(id=r[0], subject=r[1], predicate=r[2], obj=r[3], status=FactStatus(r[4]), reason=r[5], project=r[6], created_at=r[7], supersedes=r[8])
            for r in rows
        ]
