"""
benchmarks/context_comparison/metrics -- metricas compartilhadas por
todos os adapters/runners deste benchmark.

Tokenizacao: reaproveita mcp_server/tokens.py (mesmo tokenizador real
SmolLM2-360M-Instruct, local_files_only=True, com fallback heuristico
declarado -- ja' validado no trabalho anterior do mcp_server). Nao
duplica a logica, so' importa.
"""
from __future__ import annotations

import statistics as st
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # raiz do AIR (pra' achar mcp_server/, core/ etc.)

from mcp_server.tokens import count_tokens  # noqa: E402


def token_count(text: str) -> dict:
    """Wrapper fino -- devolve sempre {'tokens': int, 'method': str},
    method e' 'tokenizer:...' ou 'heuristic_chars_div_4', nunca escondido."""
    return count_tokens(text or "")


def is_correct(generated_text: str, expected_answer: str) -> bool:
    """Scoring por substring case-insensitive. Nao e' um juiz semantico
    (nao ha' um disponivel de confianca nesta maquina) -- e' por isso que
    o dataset foi desenhado com respostas curtas e canonicas (nome, termo
    tecnico, data curta), pra' esse metodo simples ser honesto o
    suficiente. Declarado explicitamente no README do benchmark."""
    if not generated_text or not expected_answer:
        return False
    return expected_answer.strip().lower() in generated_text.strip().lower()


@dataclass
class CaseResult:
    case_id: str
    category: str
    difficulty: str
    approach: str
    correct: bool
    original_input_tokens: int
    final_input_tokens: int
    output_tokens: int
    retrieval_latency_ms: float = 0.0
    compression_latency_ms: float = 0.0
    reconstruction_latency_ms: float = 0.0
    model_latency_ms: float = 0.0
    total_latency_ms: float = 0.0
    generated_text: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.final_input_tokens + self.output_tokens

    @property
    def reduction_percent(self) -> float:
        if self.original_input_tokens <= 0:
            return 0.0
        return 1 - (self.final_input_tokens / self.original_input_tokens)

    @property
    def compression_ratio(self) -> float:
        if self.final_input_tokens <= 0:
            return float("inf")
        return self.original_input_tokens / self.final_input_tokens


def aggregate(results: list[CaseResult]) -> dict:
    """Agrega uma lista de CaseResult (de UMA abordagem) em estatisticas
    -- media, desvio, N -- nunca uma unica execucao apresentada como
    prova (regra 15 do pedido)."""
    if not results:
        return {}
    accs = [1.0 if r.correct else 0.0 for r in results]
    orig_tok = [r.original_input_tokens for r in results]
    final_tok = [r.final_input_tokens for r in results]
    total_lat = [r.total_latency_ms for r in results]
    reductions = [r.reduction_percent for r in results]

    return {
        "n": len(results),
        "accuracy_mean": st.mean(accs),
        "accuracy_stdev": st.stdev(accs) if len(accs) > 1 else 0.0,
        "original_input_tokens_mean": st.mean(orig_tok),
        "final_input_tokens_mean": st.mean(final_tok),
        "reduction_percent_mean": st.mean(reductions),
        "total_latency_ms_mean": st.mean(total_lat),
        "total_latency_ms_stdev": st.stdev(total_lat) if len(total_lat) > 1 else 0.0,
    }


def efficiency_metrics(baseline_agg: dict, approach_agg: dict) -> dict:
    """quality_retention = compressed_accuracy / baseline_accuracy;
    token_reduction = 1 - compressed_tokens/baseline_tokens -- calculados
    separadamente e mostrados junto dos componentes, nunca so' uma
    metrica magica combinada (regra 13 do pedido)."""
    baseline_acc = baseline_agg.get("accuracy_mean", 0.0)
    approach_acc = approach_agg.get("accuracy_mean", 0.0)
    baseline_tok = baseline_agg.get("final_input_tokens_mean", 0.0)
    approach_tok = approach_agg.get("final_input_tokens_mean", 0.0)
    baseline_lat = baseline_agg.get("total_latency_ms_mean", 0.0)
    approach_lat = approach_agg.get("total_latency_ms_mean", 0.0)

    quality_retention = (approach_acc / baseline_acc) if baseline_acc > 0 else (1.0 if approach_acc == 0 else float("inf"))
    token_reduction = (1 - approach_tok / baseline_tok) if baseline_tok > 0 else 0.0
    latency_overhead_ms = approach_lat - baseline_lat
    tokens_saved = baseline_tok - approach_tok
    tokens_saved_per_ms_overhead = (tokens_saved / latency_overhead_ms) if latency_overhead_ms > 0 else None

    return {
        "quality_retention": quality_retention,
        "token_reduction": token_reduction,
        "latency_overhead_ms": latency_overhead_ms,
        "tokens_saved": tokens_saved,
        "tokens_saved_per_ms_overhead": tokens_saved_per_ms_overhead,
    }
