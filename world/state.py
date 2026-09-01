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
    project TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS relations (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    project TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    FOREIGN KEY (source_id) REFERENCES entities(id),
    FOREIGN KEY (target_id) REFERENCES entities(id)
);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    project TEXT NOT NULL DEFAULT '',
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
        self._migrate_add_project_column()
        self.conn.commit()
        # contador de versao -- incrementado em toda escrita (entity/
        # relation/event/update_entity/delete_entity). Usado so' como
        # sinal de invalidacao de cache pro indice de busca acelerado
        # (mcp_server/kakeya_index.py): "mudou desde a ultima vez que
        # busquei" em vez de reconstruir o indice a cada busca.
        self._version = 0

    def version(self) -> int:
        return self._version

    def _migrate_add_project_column(self) -> None:
        # 'project' foi adicionado depois da primeira versao do schema --
        # CREATE TABLE IF NOT EXISTS nao adiciona coluna em tabela que ja'
        # existe (ex: o storage compartilhado de producao, criado antes
        # desta mudanca). ALTER TABLE ADD COLUMN e' idempotente aqui porque
        # so' roda quando a coluna ainda nao existe -- nao apaga/recria
        # nada, dados existentes ganham project='' (mesmo default de
        # entidade/fato novo sem escopo). entities ganhou a coluna numa
        # rodada anterior; relations/events ganham agora, mesmo padrao --
        # storage de producao criado antes desta mudanca tambem migra sem
        # perder linha nenhuma.
        for table in ("entities", "relations", "events"):
            cols = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "project" not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN project TEXT NOT NULL DEFAULT ''")

    def entity(self, name: str, kind: str, attrs: dict | None = None, id: str | None = None, project: str = "") -> Entity:
        e = Entity(id=id or new_id("ent"), kind=kind, name=name, attrs=attrs or {}, project=project)
        self.conn.execute(
            "INSERT INTO entities (id, kind, name, attrs, project, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (e.id, e.kind, e.name, json.dumps(e.attrs), e.project, e.created_at),
        )
        self.conn.commit()
        self._version += 1
        return e

    def relation(self, source_id: str, kind: str, target_id: str, project: str = "") -> Relation:
        r = Relation(id=new_id("rel"), source_id=source_id, kind=kind, target_id=target_id, project=project)
        self.conn.execute(
            "INSERT INTO relations (id, source_id, kind, target_id, project, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (r.id, r.source_id, r.kind, r.target_id, r.project, r.created_at),
        )
        self.conn.commit()
        self._version += 1
        return r

    def event(self, entity_id: str, kind: str, payload: dict | None = None, project: str = "") -> Event:
        ev = Event(id=new_id("evt"), entity_id=entity_id, kind=kind, payload=payload or {}, project=project)
        self.conn.execute(
            "INSERT INTO events (id, entity_id, kind, payload, project, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (ev.id, ev.entity_id, ev.kind, json.dumps(ev.payload), ev.project, ev.created_at),
        )
        self.conn.commit()
        self._version += 1
        return ev

    # ---------------- consultas (o que resolve "o que depende da API?") ----------------

    def get_entity(self, id: str) -> Entity | None:
        row = self.conn.execute(
            "SELECT id, kind, name, attrs, project, created_at FROM entities WHERE id = ?", (id,)
        ).fetchone()
        if row is None:
            return None
        return Entity(id=row[0], kind=row[1], name=row[2], attrs=json.loads(row[3]), project=row[4], created_at=row[5])

    def find_entity_by_name(self, name: str) -> Entity | None:
        row = self.conn.execute(
            "SELECT id, kind, name, attrs, project, created_at FROM entities WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        return Entity(id=row[0], kind=row[1], name=row[2], attrs=json.loads(row[3]), project=row[4], created_at=row[5])

    def dependents_of(self, entity_id: str, relation_kind: str = "depends_on") -> list[Entity]:
        """Responde 'o que depende de X' -- consulta direta, sem
        reconstruir nada a partir de conversa, exatamente o requisito
        original."""
        rows = self.conn.execute(
            """SELECT e.id, e.kind, e.name, e.attrs, e.project, e.created_at
               FROM relations r JOIN entities e ON e.id = r.source_id
               WHERE r.target_id = ? AND r.kind = ?""",
            (entity_id, relation_kind),
        ).fetchall()
        return [Entity(id=r[0], kind=r[1], name=r[2], attrs=json.loads(r[3]), project=r[4], created_at=r[5]) for r in rows]

    def update_entity(self, id: str, attrs: dict | None = None, kind: str | None = None, merge_attrs: bool = True) -> Entity | None:
        """Atualiza uma entidade existente em vez de exigir delete+registro
        de novo (limitacao conhecida ate' aqui -- ver README). Preserva id/
        name/project/created_at (mesma identidade, so' o conteudo muda).

        merge_attrs=True (default): novos attrs se combinam com os
        existentes (dict.update -- chave repetida usa o valor novo, chaves
        que so' existiam antes sao preservadas). merge_attrs=False:
        substitui attrs inteiro pelo que foi passado.

        Devolve None se o id nao existir (idempotente, nao levanta
        excecao, mesmo padrao de delete_entity)."""
        existing = self.get_entity(id)
        if existing is None:
            return None
        new_attrs = dict(existing.attrs)
        if attrs is not None:
            if merge_attrs:
                new_attrs.update(attrs)
            else:
                new_attrs = dict(attrs)
        new_kind = kind if kind else existing.kind
        self.conn.execute(
            "UPDATE entities SET kind = ?, attrs = ? WHERE id = ?",
            (new_kind, json.dumps(new_attrs), id),
        )
        self.conn.commit()
        self._version += 1
        return Entity(id=existing.id, kind=new_kind, name=existing.name, attrs=new_attrs, project=existing.project, created_at=existing.created_at)

    def delete_entity(self, id: str) -> bool:
        """Remove uma entidade por id (hard delete -- diferente de
        MemoryStore.forget, Entity nao tem campo status/soft-delete no
        schema atual, entao nao ha' o que marcar). Relations/events que
        apontam pra' este id ficam orfaos (FK nao e' enforced por padrao
        no sqlite3 sem PRAGMA foreign_keys=ON, que este projeto nao liga
        de proposito -- ver historico de decisao). Devolve False se o id
        nao existia (idempotente, nao levanta excecao)."""
        cur = self.conn.execute("DELETE FROM entities WHERE id = ?", (id,))
        self.conn.commit()
        if cur.rowcount > 0:
            self._version += 1
        return cur.rowcount > 0

    def relations_of(self, entity_id: str) -> list[Relation]:
        rows = self.conn.execute(
            "SELECT id, source_id, kind, target_id, project, created_at FROM relations WHERE source_id = ? OR target_id = ?",
            (entity_id, entity_id),
        ).fetchall()
        return [Relation(id=r[0], source_id=r[1], kind=r[2], target_id=r[3], project=r[4], created_at=r[5]) for r in rows]

    def all_entities(self, project: str | None = None) -> list[Entity]:
        """Todas as entidades -- usado pela camada de retrieval
        (mcp_server/retrieval.py) pra' buscar por palavra-chave sem saber
        o id de antemao.

        project=None (default): sem filtro, devolve tudo -- comportamento
        de antes desta mudanca, quem chama sem saber de escopo continua
        funcionando igual. project="algo": devolve so' entidades desse
        projeto MAIS as globais (project=='') -- e' o que isola um mundo
        do outro sem esconder o que foi marcado de proposito como
        reutilizavel entre projetos."""
        if project is None:
            rows = self.conn.execute("SELECT id, kind, name, attrs, project, created_at FROM entities").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id, kind, name, attrs, project, created_at FROM entities WHERE project = ? OR project = ''",
                (project,),
            ).fetchall()
        return [Entity(id=r[0], kind=r[1], name=r[2], attrs=json.loads(r[3]), project=r[4], created_at=r[5]) for r in rows]

    def all_events(self, limit: int = 200, project: str | None = None) -> list[Event]:
        """Todos os eventos recentes -- mesma motivacao de all_entities().

        project=None (default): sem filtro, ve tudo -- comportamento de
        antes desta mudanca. project="algo": so' eventos desse projeto MAIS
        os globais (project=='') -- fecha a lacuna que o README documentava
        ("eventos ainda nao sao filtrados por project"): agora sao, mesmo
        padrao ja' usado em all_entities/memory.all_active."""
        if project is None:
            rows = self.conn.execute(
                "SELECT id, entity_id, kind, payload, project, created_at FROM events ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id, entity_id, kind, payload, project, created_at FROM events WHERE project = ? OR project = '' ORDER BY created_at DESC LIMIT ?",
                (project, limit),
            ).fetchall()
        return [Event(id=r[0], entity_id=r[1], kind=r[2], payload=json.loads(r[3]), project=r[4], created_at=r[5]) for r in rows]

    def count_entities(self, project: str | None = None) -> int:
        """Mesma motivacao de memory.MemoryStore.count_active: contagem
        sem buscar/construir Entity nenhuma, pro accounting de
        mcp_server/retrieval.py."""
        if project is None:
            row = self.conn.execute("SELECT COUNT(*) FROM entities").fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) FROM entities WHERE project = ? OR project = ''", (project,)).fetchone()
        return row[0]

    def count_events(self, limit: int = 200, project: str | None = None) -> int:
        """Mesma motivacao, pra' events -- respeita o mesmo `limit` de
        all_events() pra' o numero continuar comparavel (nao teria sentido
        contar TODO evento do banco se a busca so' olha os `limit` mais
        recentes)."""
        if project is None:
            row = self.conn.execute("SELECT COUNT(*) FROM (SELECT id FROM events ORDER BY created_at DESC LIMIT ?)", (limit,)).fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM (SELECT id FROM events WHERE project = ? OR project = '' ORDER BY created_at DESC LIMIT ?)",
                (project, limit),
            ).fetchone()
        return row[0]

    def get_entities_by_ids(self, ids: set[str], project: str | None = None) -> list[Entity]:
        """Mesma motivacao de memory.MemoryStore.get_facts_by_ids: busca
        so' as entidades cujo id ja' foi reduzido pelo indice de bissecao
        (mcp_server/kakeya_index.py), em vez de all_entities() inteiro
        seguido de descarte. IN (...) em lotes de ate' 400 -- mesmo limite
        de sqlite3 (SQLITE_MAX_VARIABLE_NUMBER=999 default)."""
        if not ids:
            return []
        id_list = list(ids)
        out: list[Entity] = []
        for start in range(0, len(id_list), 400):
            chunk = id_list[start:start + 400]
            placeholders = ",".join("?" for _ in chunk)
            clauses = [f"id IN ({placeholders})"]
            params: list = list(chunk)
            if project is not None:
                clauses.append("(project = ? OR project = '')")
                params.append(project)
            where = " AND ".join(clauses)
            rows = self.conn.execute(
                f"SELECT id, kind, name, attrs, project, created_at FROM entities WHERE {where}", params
            ).fetchall()
            out.extend(
                Entity(id=r[0], kind=r[1], name=r[2], attrs=json.loads(r[3]), project=r[4], created_at=r[5])
                for r in rows
            )
        return out

    def get_events_by_ids(self, ids: set[str], project: str | None = None) -> list[Event]:
        """Mesma motivacao/padrao de get_entities_by_ids, pra' events."""
        if not ids:
            return []
        id_list = list(ids)
        out: list[Event] = []
        for start in range(0, len(id_list), 400):
            chunk = id_list[start:start + 400]
            placeholders = ",".join("?" for _ in chunk)
            clauses = [f"id IN ({placeholders})"]
            params: list = list(chunk)
            if project is not None:
                clauses.append("(project = ? OR project = '')")
                params.append(project)
            where = " AND ".join(clauses)
            rows = self.conn.execute(
                f"SELECT id, entity_id, kind, payload, project, created_at FROM events WHERE {where}", params
            ).fetchall()
            out.extend(
                Event(id=r[0], entity_id=r[1], kind=r[2], payload=json.loads(r[3]), project=r[4], created_at=r[5])
                for r in rows
            )
        return out

    def events_of(self, entity_id: str, limit: int = 50) -> list[Event]:
        rows = self.conn.execute(
            "SELECT id, entity_id, kind, payload, project, created_at FROM events WHERE entity_id = ? ORDER BY created_at DESC LIMIT ?",
            (entity_id, limit),
        ).fetchall()
        return [Event(id=r[0], entity_id=r[1], kind=r[2], payload=json.loads(r[3]), project=r[4], created_at=r[5]) for r in rows]
