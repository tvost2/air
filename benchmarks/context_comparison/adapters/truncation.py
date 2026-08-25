"""
benchmarks/context_comparison/adapters -- Context Truncation, baseline
"extremamente simples" pedida explicitamente (regra 3): corta o contexto
num limite de caracteres, sem nenhuma inteligencia de selecao. Testa
mais de um limite (parametrizado), pra' mostrar a curva
reducao-vs-acuracia de um metodo ingenuo.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[2]))
sys.path.insert(0, str(_HERE.parents[0]))

import shared_model  # noqa: E402
from metrics.metrics import CaseResult, is_correct, token_count  # noqa: E402


def name(limit_chars: int) -> str:
    return f"truncation_{limit_chars}"


def run_case(case: dict, original_input_tokens: int, limit_chars: int) -> CaseResult:
    import time

    t0 = time.perf_counter()
    truncated = case["context"][:limit_chars]
    reconstruction_ms = (time.perf_counter() - t0) * 1000

    prompt = shared_model.build_prompt(truncated, case["question"])
    final_tokens = token_count(prompt)["tokens"]

    text, model_ms = shared_model.answer(truncated, case["question"])
    output_tokens = token_count(text)["tokens"]

    return CaseResult(
        case_id=case["id"], category=case["category"], difficulty=case["difficulty"], approach=name(limit_chars),
        correct=is_correct(text, case["expected_answer"]),
        original_input_tokens=original_input_tokens,
        final_input_tokens=final_tokens,
        output_tokens=output_tokens,
        reconstruction_latency_ms=reconstruction_ms,
        model_latency_ms=model_ms,
        total_latency_ms=reconstruction_ms + model_ms,
        generated_text=text,
        extra={"limit_chars": limit_chars, "context_was_truncated": len(case["context"]) > limit_chars},
    )
