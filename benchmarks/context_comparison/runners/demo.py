"""
benchmarks/context_comparison/runners/demo.py -- demo pedida
explicitamente (regra 24): Full Context, AIR e Semantic RAG respondendo
a MESMA pergunta sobre o MESMO contexto, mostrando tokens/reducao/
acuracia/latencia lado a lado.

Usa um caso real do dataset (nao um exemplo separado inventado), pra'
ficar reproduzivel e consistente com o resto do benchmark.

    python -m benchmarks.context_comparison.runners.demo [case_id]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CC_ROOT = _HERE.parents[0]
_AIR_ROOT = _HERE.parents[1]

sys.path.insert(0, str(_CC_ROOT / "adapters"))
sys.path.insert(0, str(_AIR_ROOT))
sys.path.insert(0, str(_CC_ROOT))

import shared_model  # noqa: E402
import full_context  # noqa: E402
import air_adapter  # noqa: E402
import semantic_rag  # noqa: E402


def load_dataset() -> list[dict]:
    return json.loads((_CC_ROOT / "datasets" / "dataset.json").read_text(encoding="utf-8"))


def print_result(label: str, r) -> None:
    print(f"\n--- {label} ---")
    print(f"tokens = {r.final_input_tokens}")
    if r.original_input_tokens:
        print(f"reduction = {r.reduction_percent:+.1%}")
    print(f"accuracy (este caso) = {'correto' if r.correct else 'incorreto'} (esperado: ver acima)")
    print(f"latency = {r.total_latency_ms:.1f} ms")
    print(f"resposta gerada = {r.generated_text.strip()[:120]!r}")


def main():
    case_id = sys.argv[1] if len(sys.argv) > 1 else "long_distance_01"
    dataset = load_dataset()
    case = next((c for c in dataset if c["id"] == case_id), None)
    if case is None:
        print(f"case_id '{case_id}' nao encontrado. Exemplos disponiveis: {[c['id'] for c in dataset[:5]]}...")
        sys.exit(1)

    print(f"Caso: {case['id']} (categoria: {case['category']}, dificuldade: {case['difficulty']})")
    print(f"Pergunta: {case['question']}")
    print(f"Resposta esperada: {case['expected_answer']}")
    print(f"Tamanho do contexto bruto: {len(case['context'])} chars")

    print("\nAquecendo modelo compartilhado...")
    shared_model.get_model()

    r_baseline = full_context.run_case(case)
    print_result("Full Context (baseline)", r_baseline)

    r_air = air_adapter.run_case(case, r_baseline.original_input_tokens, structural=True)
    print_result("AIR + Structural Memory", r_air)

    if semantic_rag.is_available():
        semantic_rag.retrieve("aquecimento", "aquecimento", top_k=1)  # aquece o embedder fora do timing
        r_rag = semantic_rag.run_case(case, r_baseline.original_input_tokens)
        print_result("Semantic RAG", r_rag)
    else:
        print(f"\n--- Semantic RAG --- NOT RUN: {semantic_rag.unavailable_reason()}")

    print("\n=== Resumo ===")
    print(f"{'Abordagem':<25} {'Tokens':>8} {'Reducao':>10} {'Correto':>10} {'Latencia(ms)':>14}")
    for label, r in [("Full Context", r_baseline), ("AIR + Structural Memory", r_air)] + ([("Semantic RAG", r_rag)] if semantic_rag.is_available() else []):
        print(f"{label:<25} {r.final_input_tokens:>8} {r.reduction_percent:>+9.1%} {str(r.correct):>10} {r.total_latency_ms:>13.1f}")


if __name__ == "__main__":
    main()
