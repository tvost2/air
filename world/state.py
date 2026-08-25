"""
AIR world -- World State: entidade/relacao/evento consultavel FORA do
prompt, como primitiva de execucao (nao memoria de retrieval).

Achado real da pesquisa (docs/ECOSYSTEM_RESEARCH.md secao 2.2): nenhum
projeto do mercado trata isso como coisa separada de "memoria pra RAG" --
tudo (Graphiti, Cognee, GraphRAG) e' memoria. Isto aqui e' peca construida
de verdade, nao adapter.

Storage: SQLite (maduro, embarcado, sem servidor -- decisao consciente,
nao reinventa banco de dados, so' o schema/API de dominio por cima).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core.types import Entity, Relation, Event, new_id, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    attrs TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS relations (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY (source_id) REFERENCES entities(id),
    FOREIGN KEY (target_id) REFERENCES entities(id)
);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);
CREATE INDEX IF NOT EXISTS idx_rel_source ON relations(source_id);
CREATE INDEX IF NOT EXISTS idx_rel_target ON relations(target_id);
CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_id);
"""


class WorldState:
    """API pretendida: world.entity(...), world.relation(...), world.event(...),
    e a pergunta central que a pesquisa confirmou que ninguem responde bem
    fora do prompt: world.depends_on(x) -- "o que depende de X?"."""

    def __init__(self, db_path: str | Path = ":memory:"):
        # check_same_thread=False: necessario pro caso de uso do
        # mcp_server/ (SDK MCP despacha cada tool call sync num worker
        # thread via anyio.to_thread.run_sync -- sem isso, sqlite3 recusa
        # usar a conexao fora da thread que a criou). Nao muda
        # comportamento pra' quem usa WorldState de uma unica thread
        # (sdk/agent.py, tests/), so' remove a restricao pra' quem
        # legitimamente precisa de outra thread.
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def entity(self, name: str, kind: str, attrs: dict | None = None, id: str | None = None) -> Entity:
        e = Entity(id=id or new_id("ent"), kind=kind, name=name, attrs=attrs or {})
        self.conn.execute(
            "INSERT INTO entities (id, kind, name, attrs, created_at) VALUES (?, ?, ?, ?, ?)",
            (e.id, e.kind, e.name, json.dumps(e.attrs), e.created_at),
        )
        self.conn.commit()
        return e

    def relation(self, source_id: str, kind: str, target_id: str) -> Relation:
        r = Relation(id=new_id("rel"), source_id=source_id, kind=kind, target_id=target_id)
        self.conn.execute(
            "INSERT INTO relations (id, source_id, kind, target_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (r.id, r.source_id, r.kind, r.target_id, r.created_at),
        )
        self.conn.commit()
        return r

    def event(self, entity_id: str, kind: str, payload: dict | None = None) -> Event:
        ev = Event(id=new_id("evt"), entity_id=entity_id, kind=kind, payload=payload or {})
        self.conn.execute(
            "INSERT INTO events (id, entity_id, kind, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (ev.id, ev.entity_id, ev.kind, json.dumps(ev.payload), ev.created_at),
        )
        self.conn.commit()
        return ev

    # ---------------- consultas (o que resolve "o que depende da API?") ----------------

    def get_entity(self, id: str) -> Entity | None:
        row = self.conn.execute(
            "SELECT id, kind, name, attrs, created_at FROM entities WHERE id = ?", (id,)
        ).fetchone()
        if row is None:
            return None
        return Entity(id=row[0], kind=row[1], name=row[2], attrs=json.loads(row[3]), created_at=row[4])

    def find_entity_by_name(self, name: str) -> Entity | None:
        row = self.conn.execute(
            "SELECT id, kind, name, attrs, created_at FROM entities WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        return Entity(id=row[0], kind=row[1], name=row[2], attrs=json.loads(row[3]), created_at=row[4])

    def dependents_of(self, entity_id: str, relation_kind: str = "depends_on") -> list[Entity]:
        """Responde 'o que depende de X' -- consulta direta, sem
        reconstruir nada a partir de conversa, exatamente o requisito
        original."""
        rows = self.conn.execute(
            """SELECT e.id, e.kind, e.name, e.attrs, e.created_at
               FROM relations r JOIN entities e ON e.id = r.source_id
               WHERE r.target_id = ? AND r.kind = ?""",
            (entity_id, relation_kind),
        ).fetchall()
        return [Entity(id=r[0], kind=r[1], name=r[2], attrs=json.loads(r[3]), created_at=r[4]) for r in rows]

    def relations_of(self, entity_id: str) -> list[Relation]:
        rows = self.conn.execute(
            "SELECT id, source_id, kind, target_id, created_at FROM relations WHERE source_id = ? OR target_id = ?",
            (entity_id, entity_id),
        ).fetchall()
        return [Relation(id=r[0], source_id=r[1], kind=r[2], target_id=r[3], created_at=r[4]) for r in rows]

    def all_entities(self) -> list[Entity]:
        """Todas as entidades -- usado pela camada de retrieval
        (mcp_server/retrieval.py) pra' buscar por palavra-chave sem saber
        o id de antemao."""
        rows = self.conn.execute("SELECT id, kind, name, attrs, created_at FROM entities").fetchall()
        return [Entity(id=r[0], kind=r[1], name=r[2], attrs=json.loads(r[3]), created_at=r[4]) for r in rows]

    def all_events(self, limit: int = 200) -> list[Event]:
        """Todos os eventos recentes, sem filtro de entidade -- mesma
        motivacao de all_entities()."""
        rows = self.conn.execute(
            "SELECT id, entity_id, kind, payload, created_at FROM events ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [Event(id=r[0], entity_id=r[1], kind=r[2], payload=json.loads(r[3]), created_at=r[4]) for r in rows]

    def events_of(self, entity_id: str, limit: int = 50) -> list[Event]:
        rows = self.conn.execute(
            "SELECT id, entity_id, kind, payload, created_at FROM events WHERE entity_id = ? ORDER BY created_at DESC LIMIT ?",
            (entity_id, limit),
        ).fetchall()
        return [Event(id=r[0], entity_id=r[1], kind=r[2], payload=json.loads(r[3]), created_at=r[4]) for r in rows]
