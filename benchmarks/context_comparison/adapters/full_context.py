"""
benchmarks/context_comparison/adapters -- Full Context / baseline.

Fluxo: context completo -> modelo -> resposta. Nao ha' reducao nenhuma
-- e' a REFERENCIA usada pra' calcular reduction_percent de todas as
outras abordagens (o prompt completo desta abordagem, tokenizado de
verdade, e' o 'original_input_tokens' de cada caso).
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent          # .../air/benchmarks/context_comparison/adapters
sys.path.insert(0, str(_HERE))                   # pra' 'import shared_model' (sibling, evita colidir com o pacote air/adapters/ vazio que ja existe na raiz do AIR)
sys.path.insert(0, str(_HERE.parents[2]))        # raiz do AIR (.../air), pra' 'core', 'models' etc.
sys.path.insert(0, str(_HERE.parents[0]))        # .../context_comparison, pra' 'metrics.metrics'

import shared_model  # noqa: E402
from metrics.metrics import CaseResult, is_correct, token_count  # noqa: E402

NAME = "full_context"


def run_case(case: dict) -> CaseResult:
    prompt = shared_model.build_prompt(case["context"], case["question"])
    original_tokens = token_count(prompt)["tokens"]

    text, model_ms = shared_model.answer(case["context"], case["question"])
    output_tokens = token_count(text)["tokens"]

    return CaseResult(
        case_id=case["id"], category=case["category"], difficulty=case["difficulty"], approach=NAME,
        correct=is_correct(text, case["expected_answer"]),
        original_input_tokens=original_tokens,
        final_input_tokens=original_tokens,
        output_tokens=output_tokens,
        model_latency_ms=model_ms,
        total_latency_ms=model_ms,
        generated_text=text,
    )
