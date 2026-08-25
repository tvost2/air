"""
AIR process -- execucao de comando, camada fina de adapter (mesma logica
de filesystem/fs.py: a seguranca real vem de tools/registry.py + Capability.
EXECUTE, isto aqui e' so' o mecanismo). Isolamento de verdade pra codigo
nao confiavel deveria vir de um adapter de sandbox (Firecracker/E2B ou
gVisor/Modal, ver docs/ECOSYSTEM_RESEARCH.md secao 1 e adapters/) -- este
subprocess.run direto e' adequado pra comando local confiavel do proprio
runtime, nao pra rodar codigo arbitrario gerado pelo LLM.
"""
from __future__ import annotations

import subprocess


def run_command(command: str, timeout: float = 30.0) -> str:
    result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
    out = result.stdout
    if result.returncode != 0:
        raise RuntimeError(f"comando saiu com codigo {result.returncode}: {result.stderr.strip()}")
    return out
