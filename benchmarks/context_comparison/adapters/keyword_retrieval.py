"""
benchmarks/context_comparison/adapters -- Keyword Retrieval, baseline
lexical pura (regra 4 do pedido: "permitira' comparar diretamente com a
busca atual do AIR").

Implementacao INDEPENDENTE da de mcp_server/retrieval.py -- de proposito.
Um baseline nao deveria compartilhar codigo com o sistema que esta' sendo
avaliado (senao a comparacao mede "AIR vs AIR com menos passos", nao
"AIR vs busca lexical generica"). A logica e' parecida (overlap de
palavra, substring, case-insensitive) porque e' a abordagem lexical mais
padrao que existe, nao porque copia o codigo do AIR.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[2]))
sys.path.insert(0, str(_HERE.parents[0]))

import shared_model  # noqa: E402
from metrics.metrics import CaseResult, is_correct, token_count  # noqa: E402

NAME = "keyword_retrieval"

_WORD_RE = re.compile(r"[a-zA-ZÀ-ÿ0-9_-]+")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _tokenize(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text)}


def split_sentences(context: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT_RE.split(context) if s.strip()]


def retrieve(context: str, question: str, top_k: int = 3) -> tuple[list[str], float]:
    t0 = time.perf_counter()
    q_words = _tokenize(question)
    sentences = split_sentences(context)
    scored = [(s, len(q_words & _tokenize(s))) for s in sentences]
    scored = [s for s in scored if s[1] > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    top = [s for s, _ in scored[:top_k]]
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
        extra={"retrieved_sentences": retrieved, "top_k": top_k},
    )
