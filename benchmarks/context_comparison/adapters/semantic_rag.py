"""
benchmarks/context_comparison/adapters -- Semantic RAG, independente do
AIR (regra 5 do pedido).

Arquitetura padrao: documents -> chunking (sentenca) -> embeddings ->
vector search (cosseno) -> top-k -> modelo. Sem infraestrutura de vector
DB dedicada (Qdrant/LanceDB) porque o dataset deste benchmark e' pequeno
o bastante (contexto por caso, nao um corpus persistente) -- calcular
cosseno em memoria com numpy e' equivalente em resultado e mais simples,
documentado explicitamente aqui, nao escondido.

Modelo de embedding: sentence-transformers (biblioteca padrao da
industria pra' isso), TENTA carregar um modelo pequeno; se a biblioteca
ou o modelo nao estiver disponivel nesta maquina, marca a abordagem como
NOT RUN (ver benchmarks/context_comparison/runners/run.py) em vez de
fingir.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[2]))
sys.path.insert(0, str(_HERE.parents[0]))

import shared_model  # noqa: E402
from keyword_retrieval import split_sentences  # noqa: E402
from metrics.metrics import CaseResult, is_correct, token_count  # noqa: E402

NAME = "semantic_rag"
EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

_embedder = None
_embedder_load_failed = False
_embedder_error = ""


def is_available() -> bool:
    """Checa (e tenta carregar, uma vez) se o embedder esta' disponivel
    nesta maquina -- usado pelo runner pra' decidir NOT RUN antes de
    tentar rodar qualquer caso."""
    _get_embedder()
    return not _embedder_load_failed


def unavailable_reason() -> str:
    return _embedder_error


def _get_embedder():
    global _embedder, _embedder_load_failed, _embedder_error
    if _embedder is not None or _embedder_load_failed:
        return _embedder
    try:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer(EMBEDDING_MODEL_NAME)
    except Exception as e:
        _embedder_load_failed = True
        _embedder_error = f"{type(e).__name__}: {e}"
        _embedder = None
    return _embedder


def retrieve(context: str, question: str, top_k: int = 3) -> tuple[list[str], float]:
    import numpy as np

    embedder = _get_embedder()
    sentences = split_sentences(context)
    if not sentences:
        return [], 0.0

    t0 = time.perf_counter()
    sent_emb = embedder.encode(sentences, convert_to_numpy=True, show_progress_bar=False)
    q_emb = embedder.encode([question], convert_to_numpy=True, show_progress_bar=False)[0]

    sent_norm = sent_emb / (np.linalg.norm(sent_emb, axis=1, keepdims=True) + 1e-9)
    q_norm = q_emb / (np.linalg.norm(q_emb) + 1e-9)
    scores = sent_norm @ q_norm

    order = np.argsort(-scores)[:top_k]
    top = [sentences[i] for i in order]
    latency_ms = (time.perf_counter() - t0) * 1000
    return top, latency_ms


def run_case(case: dict, original_input_tokens: int, top_k: int = 3) -> CaseResult:
    retrieved, retrieval_ms = retrieve(case["context"], case["question"], top_k=top_k)
    reconstructed = " ".join(retrieved)

    prompt = shared_model.build_prompt(reconstructed, case["question"])
    final_tokens = token_count(prompt)["tokens"]

    text, model_ms = shared_model.answer(reconstructed, case["question"])
    output_tokens = token_count(text)["tokens"]

    return CaseResult(
        case_id=case["id"], category=case["category"], difficulty=case["difficulty"], approach=NAME,
        correct=is_correct(text, case["expected_answer"]),
        original_input_tokens=original_input_tokens,
        final_input_tokens=final_tokens,
        output_tokens=output_tokens,
        retrieval_latency_ms=retrieval_ms,
        model_latency_ms=model_ms,
        total_latency_ms=retrieval_ms + model_ms,
        generated_text=text,
        extra={"retrieved_sentences": retrieved, "top_k": top_k, "embedding_model": EMBEDDING_MODEL_NAME},
    )
