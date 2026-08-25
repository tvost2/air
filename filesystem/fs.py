"""
AIR filesystem -- funcoes puras de arquivo, pensadas pra registrar como
tools via tools/registry.py (que e' quem aplica a checagem de
Capability.FILESYSTEM por caminho -- esta camada nao reimplementa
seguranca, so' a operacao; ver docs/ECOSYSTEM_RESEARCH.md secao 3: isto e'
"camada fina de adapter", nao peca central do AIR.
"""
from __future__ import annotations

from pathlib import Path


def read_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write_file(path: str, content: str) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"escrito {len(content)} chars em {path}"


def list_dir(path: str) -> str:
    entries = sorted(p.name + ("/" if p.is_dir() else "") for p in Path(path).iterdir())
    return "\n".join(entries)
