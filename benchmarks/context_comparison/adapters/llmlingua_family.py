"""
benchmarks/context_comparison/adapters -- LLMLingua e LongLLMLingua.

ACHADO REAL DE FEASIBILITY (nao suposicao -- medido nesta maquina antes
de escrever este adapter): o compressor DEFAULT da biblioteca
`llmlingua` e' NousResearch/Llama-2-7b-hf (~13GB, device_map='cuda' por
padrao). Esta maquina tem CPU-only e, no momento deste benchmark, so'
~253MB livres no disco C: (`df -h` real, nao estimativa) -- baixar o
modelo default e' fisicamente impossivel aqui, confirmado por uma
tentativa real que falhou com RuntimeError de espaco em disco
insuficiente (ver reports/ pra' o log completo).

DECISAO, disclosed explicitamente (regra 8/9 do pedido: "nao confundir
numeros publicados no paper com resultados obtidos", "registrar
configuracao completa"): PromptCompressor aceita `model_name` como
parametro -- nao e' hardcoded pro Llama-2-7b. Este adapter substitui o
compressor default por HuggingFaceTB/SmolLM2-360M-Instruct (o MESMO
modelo ja' usado em todo o resto deste benchmark/sessao, ja' em cache
local, sem download novo). Isso torna a execucao CPU-feasible de verdade
nesta maquina -- mas o algoritmo de compressao roda com um compressor
MUITO menor que o testado no paper original. Os resultados NAO devem ser
comparados aos numeros publicados no paper do LLMLingua/LongLLMLingua --
sao uma execucao real, mas de uma configuracao no-default,
explicitamente marcada como tal em todo output.
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
from metrics.metrics import CaseResult, is_correct, token_count  # noqa: E402

COMPRESSOR_MODEL_NAME = shared_model.MODEL_NAME  # substituicao deliberada, ver docstring do modulo

_compressor = None
_compressor_load_failed = False
_compressor_error = ""


def is_available() -> bool:
    _get_compressor()
    return not _compressor_load_failed


def unavailable_reason() -> str:
    return _compressor_error


def _get_compressor():
    global _compressor, _compressor_load_failed, _compressor_error
    if _compressor is not None or _compressor_load_failed:
        return _compressor
    try:
        from llmlingua import PromptCompressor
        _compressor = PromptCompressor(model_name=COMPRESSOR_MODEL_NAME, device_map="cpu")
    except Exception as e:
        _compressor_load_failed = True
        _compressor_error = f"{type(e).__name__}: {e}"
        _compressor = None
    return _compressor


def _compress(context: str, question: str, longllmlingua: bool, target_token: int = 60) -> tuple[str, float, dict]:
    pc = _get_compressor()
    t0 = time.perf_counter()
    if longllmlingua:
        # parametros recomendados pelo repositorio/paper oficial do
        # LongLLMLingua pra' compressao consciente da pergunta
        # (question-aware): condition_compare + rank_method='longllmlingua'
        # sao o que diferencia de um LLMLingua v1 generico.
        result = pc.compress_prompt(
            [context], instruction="", question=question,
            target_token=target_token,
            condition_compare=True,
            condition_in_question="after_condition",
            rank_method="longllmlingua",
            reorder_context="sort",
            dynamic_context_compression_ratio=0.3,
            context_budget="+100",
        )
    else:
        result = pc.compress_prompt(context, instruction="", question=question, target_token=target_token)
    latency_ms = (time.perf_counter() - t0) * 1000
    return result["compressed_prompt"], latency_ms, result


LLMLINGUA2_MODEL_NAME = "microsoft/llmlingua-2-xlm-roberta-large-meetingbank"

_compressor_v2 = None
_compressor_v2_load_failed = False
_compressor_v2_error = ""


def is_available_v2() -> bool:
    """LLMLingua-2 usa uma classe de modelo DIFERENTE (classificador de
    token em cima de um encoder, tipo XLM-RoBERTa) da LLMLingua v1/Long
    (perplexidade num causal LM) -- por isso e' checado/carregado
    separado do compressor de cima, nao reaproveita SmolLM2."""
    _get_compressor_v2()
    return not _compressor_v2_load_failed


def unavailable_reason_v2() -> str:
    return _compressor_v2_error


def _get_compressor_v2():
    global _compressor_v2, _compressor_v2_load_failed, _compressor_v2_error
    if _compressor_v2 is not None or _compressor_v2_load_failed:
        return _compressor_v2
    try:
        from llmlingua import PromptCompressor
        _compressor_v2 = PromptCompressor(model_name=LLMLINGUA2_MODEL_NAME, use_llmlingua2=True, device_map="cpu")
    except Exception as e:
        _compressor_v2_load_failed = True
        _compressor_v2_error = f"{type(e).__name__}: {e}"
        _compressor_v2 = None
    return _compressor_v2


def run_case_v2(case: dict, original_input_tokens: int, rate: float = 0.4) -> CaseResult:
    pc = _get_compressor_v2()
    t0 = time.perf_counter()
    try:
        raw_result = pc.compress_prompt(case["context"], rate=rate, force_tokens=["\n", ".", "?"])
    except Exception as e:
        # mesma disciplina de run_case() -- ver comentario la' pro
        # bug real ja confirmado no compressor v1/long; aqui e' so'
        # defesa, o algoritmo do v2 (classificador de token, nao
        # perplexidade causal) nao usa o mesmo caminho de codigo.
        failure_ms = (time.perf_counter() - t0) * 1000
        return CaseResult(
            case_id=case["id"], category=case["category"], difficulty=case["difficulty"], approach="llmlingua2",
            correct=False,
            original_input_tokens=original_input_tokens,
            final_input_tokens=original_input_tokens,
            output_tokens=0,
            compression_latency_ms=failure_ms,
            total_latency_ms=failure_ms,
            generated_text="",
            extra={"compression_error": f"{type(e).__name__}: {e}"},
        )
    compression_ms = (time.perf_counter() - t0) * 1000
    compressed = raw_result["compressed_prompt"]

    prompt = shared_model.build_prompt(compressed, case["question"])
    final_tokens = token_count(prompt)["tokens"]

    text, model_ms = shared_model.answer(compressed, case["question"])
    output_tokens = token_count(text)["tokens"]

    return CaseResult(
        case_id=case["id"], category=case["category"], difficulty=case["difficulty"], approach="llmlingua2",
        correct=is_correct(text, case["expected_answer"]),
        original_input_tokens=original_input_tokens,
        final_input_tokens=final_tokens,
        output_tokens=output_tokens,
        compression_latency_ms=compression_ms,
        model_latency_ms=model_ms,
        total_latency_ms=compression_ms + model_ms,
        generated_text=text,
        extra={
            "compressor_model": LLMLINGUA2_MODEL_NAME,
            "compressor_model_is_paper_default": True,  # este SIM e' o modelo nativo do LLMLingua-2, sem substituicao
            "llmlingua_reported_ratio": raw_result.get("ratio"),
            "llmlingua_reported_rate": raw_result.get("rate"),
        },
    )


def run_case(case: dict, original_input_tokens: int, longllmlingua: bool, target_token: int = 60) -> CaseResult:
    approach = "longllmlingua" if longllmlingua else "llmlingua"
    t0 = time.perf_counter()
    try:
        compressed, compression_ms, raw_result = _compress(case["context"], case["question"], longllmlingua, target_token)
    except Exception as e:
        # achado real (nao hipotetico): llmlingua==0.2.2 quebra em
        # contextos longos (categoria long_distance) com
        # ValueError: too many values to unpack (expected 2) dentro de
        # iterative_compress_prompt -- incompatibilidade entre o formato
        # de past_key_values esperado pela biblioteca e o retornado pelo
        # transformers==5.15.1 instalado nesta maquina. Em vez de deixar
        # o benchmark inteiro cair por causa de UM caso, registra a
        # falha como resultado (correct=False, sem reducao de token
        # alcancada) e continua -- e' um achado honesto sobre a
        # abordagem, nao um bug do benchmark a esconder.
        failure_ms = (time.perf_counter() - t0) * 1000
        return CaseResult(
            case_id=case["id"], category=case["category"], difficulty=case["difficulty"], approach=approach,
            correct=False,
            original_input_tokens=original_input_tokens,
            final_input_tokens=original_input_tokens,  # compressao falhou -- nenhuma reducao foi de fato alcancada
            output_tokens=0,
            compression_latency_ms=failure_ms,
            total_latency_ms=failure_ms,
            generated_text="",
            extra={"compression_error": f"{type(e).__name__}: {e}"},
        )

    prompt = shared_model.build_prompt(compressed, case["question"])
    final_tokens = token_count(prompt)["tokens"]

    text, model_ms = shared_model.answer(compressed, case["question"])
    output_tokens = token_count(text)["tokens"]

    return CaseResult(
        case_id=case["id"], category=case["category"], difficulty=case["difficulty"], approach=approach,
        correct=is_correct(text, case["expected_answer"]),
        original_input_tokens=original_input_tokens,
        final_input_tokens=final_tokens,
        output_tokens=output_tokens,
        compression_latency_ms=compression_ms,
        model_latency_ms=model_ms,
        total_latency_ms=compression_ms + model_ms,
        generated_text=text,
        extra={
            "compressor_model": COMPRESSOR_MODEL_NAME,
            "compressor_model_is_paper_default": False,
            "llmlingua_reported_ratio": raw_result.get("ratio"),
            "llmlingua_reported_rate": raw_result.get("rate"),
            "compressed_prompt_preview": compressed[:200],
        },
    )
