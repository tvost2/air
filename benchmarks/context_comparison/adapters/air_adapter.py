"""
benchmarks/context_comparison/adapters -- AIR e AIR + Structural Memory.

Usa mcp_server/adapter.py::AirAdapter DIRETAMENTE, sem nenhuma
modificacao de logica (regra 6 do pedido: "executar o AIR atual sem
modificar sua logica para favorecer o benchmark") -- e' literalmente o
mesmo codigo usado pelo servidor MCP de producao.

Ingestao: cada caso vira uma instancia ISOLADA de AirAdapter (SQLite
em memoria, nao compartilha estado entre casos). As sentencas de
'sentence_keys' do caso (ver datasets/synthetic.py) sao gravadas via
air.store_memory() com o subject/predicate que um usuario real daria --
isso e' o que deixa o mecanismo de recencia do AIR (supersede por
subject+predicate igual) realmente funcionar pra' valer na categoria
recency_conflict, em vez de testar uma versao capada do AIR. Sentencas
de preenchimento (sem entrada em sentence_keys) tambem sao gravadas,
com subject/predicate unicos por sentenca -- viram ruido de memoria que
o retrieval do AIR precisa filtrar, do mesmo jeito que as outras
abordagens tem que lidar com o texto de preenchimento no meio do
contexto bruto.

Duas variantes, numeros SEPARADOS (regra 7: "nao misturar os numeros"):
- air (structural=False): AIR_ENABLE_STRUCTURAL_MEMORY=false -> fatos
  renderizados em prosa.
- air_structural_memory (structural=True): fatos renderizados como
  FACT(subject,predicate,valor) -- mesma notacao validada em
  struct-reasoning/memory (N=200: +12pp acuracia, -31.6% tokens vs prosa).
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
from mcp_server.adapter import AirAdapter  # noqa: E402
from mcp_server.config import Config  # noqa: E402


def name(structural: bool) -> str:
    return "air_structural_memory" if structural else "air"


def _ingest(air: AirAdapter, case: dict) -> float:
    """Grava o contexto do caso como fatos discretos, do jeito que um
    usuario real do AIR contaria essa informacao ao longo do tempo (nao
    'joga o paragrafo inteiro'). Devolve o tempo de ingestao em ms --
    contado SEPARADO da latencia de consulta, porque na pratica ingestao
    e' custo pago uma vez (quando o fato acontece), nao a cada pergunta."""
    t0 = time.perf_counter()

    keyed_texts = {sk["text"] for sk in case.get("sentence_keys", [])}
    for sk in case.get("sentence_keys", []):
        air.store_memory(sk["text"], metadata={"subject": sk["subject"], "predicate": sk["predicate"]})

    # sentencas de preenchimento (sem chave estruturada no dataset) --
    # viram notas com subject/predicate unicos, ruido de memoria real
    from keyword_retrieval import split_sentences
    for i, sentence in enumerate(split_sentences(case["context"])):
        if sentence in keyed_texts:
            continue
        air.store_memory(sentence, metadata={"subject": f"note:{case['id']}", "predicate": f"filler_{i}"})

    return (time.perf_counter() - t0) * 1000


def run_case(case: dict, original_input_tokens: int, structural: bool, max_tokens: int = 500) -> CaseResult:
    cfg = Config()
    cfg.storage_path = ":memory:"
    cfg.enable_structural_memory = structural
    air = AirAdapter(cfg)

    ingestion_ms = _ingest(air, case)

    ctx_result = air.get_context(case["question"], max_tokens=max_tokens)
    reconstructed = ctx_result.get("context", "")
    retrieval_ms = ctx_result.get("latency_ms", 0.0)

    prompt = shared_model.build_prompt(reconstructed, case["question"])
    final_tokens = token_count(prompt)["tokens"]

    text, model_ms = shared_model.answer(reconstructed, case["question"])
    output_tokens = token_count(text)["tokens"]

    return CaseResult(
        case_id=case["id"], category=case["category"], difficulty=case["difficulty"], approach=name(structural),
        correct=is_correct(text, case["expected_answer"]),
        original_input_tokens=original_input_tokens,
        final_input_tokens=final_tokens,
        output_tokens=output_tokens,
        retrieval_latency_ms=retrieval_ms,
        model_latency_ms=model_ms,
        total_latency_ms=retrieval_ms + model_ms,  # ingestion NAO entra aqui de proposito -- ver docstring de _ingest
        generated_text=text,
        extra={
            "ingestion_latency_ms": ingestion_ms,
            "reference_count": ctx_result.get("reference_count", 0),
            "planner": ctx_result.get("planner", []),
            "structural_memory_enabled": structural,
        },
    )
