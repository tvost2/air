"""
AIR mcp_server -- configuracao por variavel de ambiente.

Nome da pasta e' 'mcp_server', nao 'mcp' -- proposito: o SDK oficial que
este servidor usa se chama 'mcp' (pip install mcp); se esta pasta se
chamasse 'mcp' e o processo fosse iniciado com cwd em E:\\x\\air, o import
`import mcp.server...` resolveria pra' esta pasta local em vez do pacote
instalado (namespace package sombreando o pacote real). Nome diferente
evita a colisao.

Nenhuma chave de API/secret e' lida ou usada aqui -- este servidor MCP
so' fala com o AIR (SQLite local), nao com nenhum provider de LLM.
"""
from __future__ import annotations

import os
from pathlib import Path

# raiz do projeto AIR (pasta que contem mcp_server/, world/, memory/ etc.)
AIR_ROOT = Path(__file__).resolve().parent.parent


def _bool_env(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _int_env(name: str, default: int) -> int:
    val = os.environ.get(name)
    if val is None or not val.strip():
        return default
    try:
        return int(val)
    except ValueError:
        return default


class Config:
    """Le' env vars uma vez, na construcao -- nao a cada acesso, pra'
    comportamento previsivel dentro de um mesmo processo de servidor."""

    def __init__(self):
        # AIR_STORAGE: caminho pro arquivo SQLite compartilhado por
        # WorldState e MemoryStore (tabelas nao colidem -- entities/
        # relations/events vs facts -- entao um unico arquivo serve aos
        # dois, reaproveitando o storage que ja existe no AIR em vez de
        # inventar um novo backend). Path default e' ANCORADO na raiz do
        # projeto (nao em cwd) porque o Claude Code pode iniciar este
        # processo com qualquer diretorio de trabalho.
        storage_env = os.environ.get("AIR_STORAGE", "").strip()
        if storage_env:
            self.storage_path = Path(storage_env)
        else:
            self.storage_path = AIR_ROOT / "storage" / "air_mcp.db"

        self.log_level = os.environ.get("AIR_LOG_LEVEL", "INFO").strip().upper()
        self.max_context_tokens = _int_env("AIR_MAX_CONTEXT", 2000)
        self.enable_structural_memory = _bool_env("AIR_ENABLE_STRUCTURAL_MEMORY", True)

        # limites de validacao de entrada (seguranca -- rule 19: validar
        # entradas, evitar abuso, sem introduzir infraestrutura pesada)
        self.max_content_chars = _int_env("AIR_MAX_CONTENT_CHARS", 20_000)
        self.max_query_chars = _int_env("AIR_MAX_QUERY_CHARS", 1_000)
        self.default_search_limit = _int_env("AIR_DEFAULT_SEARCH_LIMIT", 5)

    def ensure_storage_dir(self) -> None:
        # ":memory:" e' o valor especial do sqlite3 pra' banco em RAM --
        # WorldState/MemoryStore ja' aceitam isso como db_path (e' o
        # default dos dois), mas nada aqui tratava o caso de alguem
        # atribuir esse literal a storage_path (string, nao Path) antes
        # desta correcao -- .parent.mkdir() quebrava com AttributeError.
        # Descoberto rodando benchmarks/context_comparison (cada caso usa
        # uma instancia isolada em memoria, sem tocar disco).
        if str(self.storage_path) == ":memory:":
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)


config = Config()
