"""
benchmarks/context_comparison/adapters -- modelo final compartilhado por
TODAS as abordagens (regra 11 do pedido: "sempre que possivel, usar
exatamente o mesmo modelo final pra' todas as abordagens" -- comparar
AIR+modeloA contra RAG+modeloB invalidaria a comparacao).

Reaproveita models/provider.py::HFLocalProvider, ja' construido e
validado no trabalho anterior do AIR (mesmo SmolLM2-360M-Instruct usado
em struct-reasoning e no benchmark de token do proprio AIR) -- carregado
uma unica vez (singleton) porque o load sozinho custa ~1-2min nesta
maquina, e o benchmark roda a MESMA instancia contra dezenas de casos.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ACHADO REAL (nao hipotetico): o disco C: desta maquina tem ~253MB
# livres (`df -h` medido durante este trabalho), insuficiente pra' baixar
# qualquer modelo novo (o cache HF default fica em C:\Users\...\.cache).
# E: tem ~49GB livres. setdefault (nao um set direto) -- se o usuario ja'
# tiver HF_HOME configurado, essa escolha e' respeitada, nao sobrescrita.
# SmolLM2-360M-Instruct (ja' usado em struct-reasoning/AIR antes deste
# benchmark) foi copiado pro cache em E: uma vez, entao isto NAO causa
# re-download do modelo principal -- so' evita que downloads NOVOS
# (embedding model do semantic_rag, etc) tentem ir pro C: cheio.
os.environ.setdefault("HF_HOME", "E:\\hf_cache")

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # raiz do AIR

from models.provider import HFLocalProvider  # noqa: E402

MODEL_NAME = "HuggingFaceTB/SmolLM2-360M-Instruct"
MAX_NEW_TOKENS = 20  # respostas curtas (nome, termo tecnico, data) -- mesma folga usada em struct-reasoning/memory apos o achado real de truncamento

_provider: HFLocalProvider | None = None


def get_model() -> HFLocalProvider:
    global _provider
    if _provider is None:
        _provider = HFLocalProvider(MODEL_NAME)
    return _provider


INSTRUCTION = (
    "Voce recebe um contexto e uma pergunta. Responda de forma curta e direta, "
    "usando apenas a informacao do contexto. Se houver informacoes conflitantes, "
    "use a mais recente."
)


def build_prompt(context_text: str, question: str) -> str:
    if context_text.strip():
        return f"{INSTRUCTION}\n\nContexto:\n{context_text}\n\nPergunta: {question}\nResposta:"
    return f"{INSTRUCTION}\n\nPergunta: {question}\nResposta:"


def answer(context_text: str, question: str) -> tuple[str, float]:
    """Gera a resposta e devolve (texto, latencia_ms) -- so' o passo de
    inferencia do modelo, sem incluir retrieval/compressao (essas sao
    medidas separadamente por cada adapter, pra' nao misturar as
    latencias, regra 12 do pedido)."""
    import time

    prompt = build_prompt(context_text, question)
    model = get_model()
    t0 = time.perf_counter()
    resp = model.complete(prompt, max_tokens=MAX_NEW_TOKENS)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return resp.text, elapsed_ms
