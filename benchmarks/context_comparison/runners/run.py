"""
benchmarks/context_comparison/runners/run.py -- orquestrador principal do
benchmark comparativo. Roda TODAS as abordagens tecnicamente viaveis
nesta maquina sobre o dataset sintetico (datasets/dataset.json) e grava
resultados brutos + agregados em reports/.

Uso:
    python -m benchmarks.context_comparison.runners.run --offline
    python -m benchmarks.context_comparison.runners.run --offline --repeats 3
    python -m benchmarks.context_comparison.runners.run --provider anthropic   (regra 20 -- ver nota abaixo)
    python -m benchmarks.context_comparison.runners.run --llmlingua2-subset 12

--offline: roda so' abordagens locais (tudo, exceto --provider). E' o
modo default se nenhuma flag de provider for passada.

--provider anthropic: NAO gera numeros falsos. Se ANTHROPIC_API_KEY nao
estiver no ambiente, a corrida com esse provider e' marcada NOT RUN com
o motivo, exatamente como qualquer outra abordagem inviavel (regra 19/20
do pedido). Nenhuma chave e' lida do codigo -- so' de variavel de
ambiente, nunca logada.

LLMLingua-2 nativo (compressor sem substituicao) mede ~227s/caso nesta
maquina (medido, nao estimado) -- rodar as 48 perguntas custaria ~3h so'
de compressao. Por padrao roda em subconjunto (--llmlingua2-subset,
default 12 = 2 por categoria) pra' manter o benchmark reproduzivel em
tempo pratico; o relatorio final declara N menor pra' essa linha
especificamente, nao esconde a diferenca de amostra.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent                  # .../context_comparison/runners
_CC_ROOT = _HERE.parents[0]                               # .../context_comparison
_AIR_ROOT = _HERE.parents[1]                               # .../air

sys.path.insert(0, str(_CC_ROOT / "adapters"))
sys.path.insert(0, str(_AIR_ROOT))
sys.path.insert(0, str(_CC_ROOT))

import shared_model  # noqa: E402
import full_context  # noqa: E402
import truncation  # noqa: E402
import keyword_retrieval  # noqa: E402
import semantic_rag  # noqa: E402
import air_adapter  # noqa: E402
import llmlingua_family  # noqa: E402
from metrics.metrics import aggregate, efficiency_metrics  # noqa: E402

DATASET_PATH = _CC_ROOT / "datasets" / "dataset.json"
REPORTS_DIR = _CC_ROOT / "reports"

TRUNCATION_LIMITS = (200, 500, 1000)


def load_dataset() -> list[dict]:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def select_llmlingua2_subset(dataset: list[dict], n_per_category: int) -> list[dict]:
    by_cat: dict[str, list[dict]] = {}
    for c in dataset:
        by_cat.setdefault(c["category"], []).append(c)
    subset = []
    for cat, cases in by_cat.items():
        subset.extend(cases[:n_per_category])
    return subset


def run_offline(dataset: list[dict], llmlingua2_subset: list[dict], repeats: int, log) -> tuple[dict, dict]:
    """Devolve (results_by_approach, availability) -- results_by_approach
    e' {approach: [CaseResult,...]}, availability e' {approach:
    {'run': bool, 'reason': str, 'n': int}}."""
    results: dict[str, list] = {}
    availability: dict[str, dict] = {}

    log("Aquecendo modelo compartilhado (SmolLM2-360M-Instruct)...")
    shared_model.get_model()

    semantic_ok = semantic_rag.is_available()
    if semantic_ok:
        semantic_rag.retrieve("aquecimento do embedder.", "aquecimento", top_k=1)  # forca carregar o modelo agora, fora do timing por caso
    else:
        availability["semantic_rag"] = {"run": False, "reason": semantic_rag.unavailable_reason(), "n": 0}
        log(f"semantic_rag INDISPONIVEL: {semantic_rag.unavailable_reason()}")

    llmlingua_ok = llmlingua_family.is_available()
    if not llmlingua_ok:
        reason = llmlingua_family.unavailable_reason()
        for approach in ("llmlingua", "longllmlingua"):
            availability[approach] = {"run": False, "reason": reason, "n": 0}
        log(f"llmlingua/longllmlingua INDISPONIVEL: {reason}")

    llmlingua2_ok = llmlingua_family.is_available_v2()
    if not llmlingua2_ok:
        availability["llmlingua2"] = {"run": False, "reason": llmlingua_family.unavailable_reason_v2(), "n": 0}
        log(f"llmlingua2 INDISPONIVEL: {llmlingua_family.unavailable_reason_v2()}")

    total_cases = len(dataset)
    for rep in range(repeats):
        for i, case in enumerate(dataset):
            log(f"[rep {rep+1}/{repeats}] caso {i+1}/{total_cases}: {case['id']}")

            r0 = full_context.run_case(case)
            results.setdefault("full_context", []).append(r0)

            for limit in TRUNCATION_LIMITS:
                r = truncation.run_case(case, r0.original_input_tokens, limit_chars=limit)
                results.setdefault(truncation.name(limit), []).append(r)

            r = keyword_retrieval.run_case(case, r0.original_input_tokens)
            results.setdefault("keyword_retrieval", []).append(r)

            r = air_adapter.run_case(case, r0.original_input_tokens, structural=False)
            results.setdefault("air", []).append(r)

            r = air_adapter.run_case(case, r0.original_input_tokens, structural=True)
            results.setdefault("air_structural_memory", []).append(r)

            if semantic_ok:
                r = semantic_rag.run_case(case, r0.original_input_tokens)
                results.setdefault("semantic_rag", []).append(r)

            if llmlingua_ok:
                r = llmlingua_family.run_case(case, r0.original_input_tokens, longllmlingua=False)
                results.setdefault("llmlingua", []).append(r)
                r2 = llmlingua_family.run_case(case, r0.original_input_tokens, longllmlingua=True)
                results.setdefault("longllmlingua", []).append(r2)

    if llmlingua2_ok:
        log(f"Rodando llmlingua2 (compressor nativo, ~227s/caso medido) em subconjunto de {len(llmlingua2_subset)} casos...")
        for i, case in enumerate(llmlingua2_subset):
            log(f"  llmlingua2 caso {i+1}/{len(llmlingua2_subset)}: {case['id']}")
            r0_tokens = full_context.run_case(case).original_input_tokens
            r = llmlingua_family.run_case_v2(case, r0_tokens)
            results.setdefault("llmlingua2", []).append(r)

    for approach, rs in results.items():
        availability[approach] = {"run": True, "reason": "", "n": len(rs)}

    return results, availability


def run_online(provider: str, log) -> dict:
    """Regra 20: opcao --provider separada. Nunca inventa numero -- se a
    chave nao estiver no ambiente, marca NOT RUN com motivo, sem tentar
    adivinhar/simular resposta de API."""
    if provider == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            reason = "ANTHROPIC_API_KEY nao encontrada no ambiente -- nenhuma chamada de API foi feita, nenhum numero foi inventado."
            log(f"provider=anthropic: NOT RUN ({reason})")
            return {"provider": provider, "run": False, "reason": reason}
        # Caminho real existiria aqui via mcp_server/... models/provider.py,
        # mas so' e' exercitado se uma chave real estiver presente --
        # nao implementado neste benchmark porque nao havia chave
        # disponivel nesta sessao pra' testar de verdade (rule: nao
        # simular, so' registrar NOT RUN com motivo).
        reason = "Caminho de execucao online nao exercitado nesta sessao (chave presente mas fluxo de chamada real nao testado aqui)."
        log(f"provider=anthropic: NOT RUN ({reason})")
        return {"provider": provider, "run": False, "reason": reason}
    return {"provider": provider, "run": False, "reason": f"provider '{provider}' nao suportado por este runner"}


def build_summary(results: dict, availability: dict) -> dict:
    baseline_agg = aggregate(results.get("full_context", []))
    summary = {"baseline": {"approach": "full_context", **baseline_agg}, "approaches": {}}
    for approach, rs in results.items():
        agg = aggregate(rs)
        eff = efficiency_metrics(baseline_agg, agg) if approach != "full_context" else {
            "quality_retention": 1.0, "token_reduction": 0.0, "latency_overhead_ms": 0.0, "tokens_saved": 0.0, "tokens_saved_per_ms_overhead": None,
        }
        summary["approaches"][approach] = {**agg, **eff}
    summary["availability"] = availability
    return summary


def build_category_breakdown(results: dict) -> dict:
    breakdown = {}
    for approach, rs in results.items():
        by_cat: dict[str, list] = {}
        for r in rs:
            by_cat.setdefault(r.category, []).append(r)
        breakdown[approach] = {cat: aggregate(rs_cat) for cat, rs_cat in by_cat.items()}
    return breakdown


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="roda so' abordagens locais (default se nenhum --provider)")
    parser.add_argument("--provider", type=str, default=None, help="ex: anthropic -- roda (ou marca NOT RUN) o caminho online")
    parser.add_argument("--repeats", type=int, default=1, help="numero de execucoes completas do dataset, pra' media/desvio (regra 15)")
    parser.add_argument("--llmlingua2-subset", type=int, default=2, help="casos por categoria pra' llmlingua2 nativo (default 2 = 12 casos)")
    parser.add_argument("--out", type=str, default=None, help="prefixo dos arquivos de saida (default: reports/run)")
    args = parser.parse_args()

    def log(msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    REPORTS_DIR.mkdir(exist_ok=True)
    out_prefix = args.out or str(REPORTS_DIR / "run")

    dataset = load_dataset()
    log(f"Dataset: {len(dataset)} casos carregados de {DATASET_PATH}")

    online_result = None
    if args.provider:
        online_result = run_online(args.provider, log)
        with open(f"{out_prefix}_online.json", "w", encoding="utf-8") as f:
            json.dump(online_result, f, ensure_ascii=False, indent=2)

    if args.offline or not args.provider:
        subset = select_llmlingua2_subset(dataset, args.llmlingua2_subset)
        log(f"Subconjunto llmlingua2: {len(subset)} casos ({args.llmlingua2_subset}/categoria)")

        t0 = time.perf_counter()
        results, availability = run_offline(dataset, subset, args.repeats, log)
        total_elapsed = time.perf_counter() - t0
        log(f"Concluido em {total_elapsed:.1f}s")

        raw_path = f"{out_prefix}_raw.json"
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump({k: [asdict(r) for r in v] for k, v in results.items()}, f, ensure_ascii=False, indent=2)
        log(f"Resultados brutos salvos em {raw_path}")

        summary = build_summary(results, availability)
        summary_path = f"{out_prefix}_summary.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        log(f"Resumo salvo em {summary_path}")

        breakdown = build_category_breakdown(results)
        breakdown_path = f"{out_prefix}_by_category.json"
        with open(breakdown_path, "w", encoding="utf-8") as f:
            json.dump(breakdown, f, ensure_ascii=False, indent=2)
        log(f"Quebra por categoria salva em {breakdown_path}")

        log(f"Tempo total: {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")


if __name__ == "__main__":
    main()
