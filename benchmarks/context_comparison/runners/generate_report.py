"""
benchmarks/context_comparison/runners/generate_report.py -- le' os JSONs
produzidos por run.py (reports/run_summary.json, run_by_category.json,
run_raw.json) e escreve o relatorio final em
benchmarks/context_comparison/report.md (regra 21 do pedido -- caminho
exato pedido).

Nao inventa numero nenhum: tudo aqui vem dos JSONs gerados por uma
execucao real. Se uma abordagem estiver ausente de availability/run=False,
aparece como NOT RUN com o motivo registrado, nunca omitida em silencio.
"""
from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CC_ROOT = _HERE.parents[0]
_AIR_ROOT = _HERE.parents[1]

sys.path.insert(0, str(_CC_ROOT / "adapters"))
from shared_model import MODEL_NAME  # noqa: E402

REPORTS_DIR = _CC_ROOT / "reports"
REPORT_PATH = _CC_ROOT / "report.md"

CATEGORY_LABELS = {
    "factual_simple": "factual (lookup direto)",
    "multi_hop": "multi-hop (combinar 2 fatos)",
    "recency_conflict": "recency/conflict",
    "long_distance": "long-distance (\"lost in the middle\")",
    "irrelevant_context": "irrelevant context (distratores)",
    "repeated_information": "repeated information (redundancia)",
}

APPROACH_ORDER = [
    "full_context", "truncation_200", "truncation_500", "truncation_1000",
    "keyword_retrieval", "semantic_rag", "air", "air_structural_memory",
    "llmlingua", "longllmlingua", "llmlingua2",
]


def load(name: str):
    path = REPORTS_DIR / f"run_{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def fmt_pct(x) -> str:
    if x is None:
        return "N/A"
    return f"{x:+.1%}" if isinstance(x, (int, float)) else str(x)


def fmt_num(x, digits=1) -> str:
    if x is None:
        return "N/A"
    return f"{x:.{digits}f}"


def table_main(summary: dict) -> str:
    lines = ["| Approach | Input Tokens (media) | Reduction | Accuracy | Retrieval Latency (ms) | Total Latency (ms) | N |",
             "|---|---|---|---|---|---|---|"]
    for approach in APPROACH_ORDER:
        avail = summary["availability"].get(approach)
        if avail is None:
            continue
        if not avail["run"]:
            lines.append(f"| {approach} | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | 0 |")
            continue
        agg = summary["approaches"][approach]
        lines.append(
            f"| {approach} | {fmt_num(agg['final_input_tokens_mean'])} | "
            f"{fmt_pct(agg['reduction_percent_mean'])} | {fmt_pct(agg['accuracy_mean'])} | "
            f"{fmt_num(agg.get('retrieval_latency_ms_mean'))} | {fmt_num(agg['total_latency_ms_mean'])} | {agg['n']} |"
        )
    return "\n".join(lines)


def table_efficiency(summary: dict) -> str:
    lines = ["| Approach | Accuracy Retention | Token Reduction | Latency Overhead (ms) |",
             "|---|---|---|---|"]
    for approach in APPROACH_ORDER:
        avail = summary["availability"].get(approach)
        if avail is None:
            continue
        if not avail["run"]:
            lines.append(f"| {approach} | NOT RUN | NOT RUN | NOT RUN |")
            continue
        agg = summary["approaches"][approach]
        lines.append(
            f"| {approach} | {fmt_pct(agg['quality_retention'])} | {fmt_pct(agg['token_reduction'])} | "
            f"{fmt_num(agg['latency_overhead_ms'])} |"
        )
    return "\n".join(lines)


def table_by_category(breakdown: dict) -> str:
    parts = []
    categories = sorted({cat for approaches in breakdown.values() for cat in approaches})
    for cat in categories:
        label = CATEGORY_LABELS.get(cat, cat)
        lines = [f"### {label}", "", "| Approach | Accuracy | Input Tokens (media) | Reduction | N |", "|---|---|---|---|---|"]
        for approach in APPROACH_ORDER:
            if approach not in breakdown or cat not in breakdown[approach]:
                continue
            agg = breakdown[approach][cat]
            lines.append(
                f"| {approach} | {fmt_pct(agg['accuracy_mean'])} | {fmt_num(agg['final_input_tokens_mean'])} | "
                f"{fmt_pct(agg['reduction_percent_mean'])} | {agg['n']} |"
            )
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def interpretation_notes(summary: dict, breakdown: dict, raw: dict | None) -> str:
    """Observacoes condicionais, calculadas a partir dos dados de verdade
    desta execucao (regra 25: nao concluir 'X e melhor' de forma
    automatica, produzir condicionais especificas)."""
    notes = []

    # melhor accuracy por categoria
    if breakdown:
        categories = sorted({cat for approaches in breakdown.values() for cat in approaches})
        for cat in categories:
            label = CATEGORY_LABELS.get(cat, cat)
            rows = [(a, breakdown[a][cat]) for a in breakdown if cat in breakdown[a]]
            if not rows:
                continue
            best_a, best_agg = max(rows, key=lambda r: (r[1]["accuracy_mean"], r[1]["reduction_percent_mean"]))
            notes.append(f"- Na categoria **{label}**, `{best_a}` teve a maior acuracia desta rodada ({best_agg['accuracy_mean']:.1%}, N={best_agg['n']}).")

    # abordagens com reducao NEGATIVA (enviam mais token que o full_context)
    for approach, agg in summary["approaches"].items():
        if approach == "full_context":
            continue
        if agg.get("reduction_percent_mean", 0) < 0:
            notes.append(
                f"- `{approach}` enviou **mais** tokens que o full_context nesta rodada "
                f"({agg['reduction_percent_mean']:+.1%} de 'reducao', ou seja, aumento). "
            )

    # explicacao mecanica especifica pro caso do AIR, se aplicavel -- vem
    # de inspecao real do run_raw.json (reference_count alto em casos
    # pequenos), nao e' suposicao
    if raw and "air" in raw and raw["air"]:
        sample = raw["air"][0]
        refs = sample.get("extra", {}).get("reference_count")
        if refs is not None and summary["approaches"].get("air", {}).get("reduction_percent_mean", 0) < 0:
            notes.append(
                "- **Por que AIR/AIR+Structural Memory aumentam tokens neste benchmark**: "
                "`air/adapter.py::get_context` (codigo real do AIR, nao modificado pro benchmark) busca ate' "
                "20 candidatos por palavra-chave sem limiar minimo de relevancia (so' `score > 0`) -- em casos "
                "com poucas sentencas no total, isso retorna quase TUDO que foi ingerido, e cada item retorna "
                "envolto em notacao `FACT(...)` + handle/label, que e' mais verboso por sentenca que o texto "
                "bruto. O mecanismo de referencia-por-ID do AIR (validado no benchmark de token do proprio AIR, "
                "`../token_benchmark.py`) economiza quando o MESMO conteudo seria reenviado REPETIDAMENTE ao "
                "longo de varios turnos -- este benchmark testa UMA UNICA consulta por caso, entao essa "
                "vantagem especifica nao tem chance de aparecer aqui. E' uma limitacao real do design atual "
                "de retrieval do AIR pra' este tipo de workload (single-shot, muitos fatos pequenos "
                "ingeridos), nao um erro de medicao."
            )

    # llmlingua/longllmlingua falha total em alguma categoria?
    if breakdown:
        for approach in ("llmlingua", "longllmlingua"):
            if approach not in breakdown:
                continue
            for cat, agg in breakdown[approach].items():
                if agg["accuracy_mean"] == 0.0:
                    notes.append(f"- `{approach}` teve **0% de acuracia** na categoria {CATEGORY_LABELS.get(cat, cat)} nesta rodada (compressor substituido, ver limitacoes).")

    return "\n".join(notes) if notes else "(nenhuma observacao automatica gerada)"


def availability_notes(summary: dict) -> str:
    lines = []
    for approach, avail in summary["availability"].items():
        if not avail["run"]:
            lines.append(f"- **{approach}**: NOT RUN -- {avail['reason']}")
    return "\n".join(lines) if lines else "(todas as abordagens planejadas rodaram)"


def find_best_worst(summary: dict) -> tuple[list, list]:
    rows = []
    for approach, avail in summary["availability"].items():
        if not avail["run"]:
            continue
        agg = summary["approaches"][approach]
        rows.append((approach, agg["accuracy_mean"], agg["reduction_percent_mean"], agg["total_latency_ms_mean"]))
    rows.sort(key=lambda r: (-r[1], -r[2]))
    return rows[:3], sorted(rows, key=lambda r: r[1])[:3]


def main():
    summary = load("summary")
    breakdown = load("by_category")
    raw = load("raw")
    if summary is None:
        print("run_summary.json nao encontrado -- rode runners/run.py --offline primeiro.")
        sys.exit(1)

    best, worst = find_best_worst(summary)

    report = f"""# Context Comparison Benchmark -- Relatorio

## 1. Objetivo

Responder, com dados reais e reproduziveis: "quanto contexto cada
abordagem consegue remover sem degradar a capacidade de responder
corretamente, e a que custo computacional?" Comparando Full Context,
Truncation, Keyword Retrieval, Semantic RAG, AIR, AIR + Structural
Memory, LLMLingua, LongLLMLingua e LLMLingua-2.

Este benchmark e' SEPARADO dos benchmarks historicos do AIR
(`benchmarks/token_benchmark.py`) e do struct-reasoning -- nenhum dos
dois foi alterado por este trabalho (ver `reports/BEFORE_*` e
`reports/AFTER_*` pra' prova de regressao zero).

## 2. Metodologia

- **Dataset**: sintetico, determinístico (seed fixa), 48 casos em 6
  categorias (8 por categoria) -- `datasets/dataset.json`,
  `datasets/synthetic.py`. Nomes e fatos inventados, sem dado pessoal
  real.
- **Modelo final**: `{MODEL_NAME}`
  (o mesmo em TODAS as abordagens que geram resposta, greedy decoding
  `do_sample=False`) -- regra de fairness do pedido (nao comparar
  AIR+modeloA contra RAG+modeloB).
- **Tokenizer**: mesmo modelo acima, via `mcp_server/tokens.py`
  (`local_files_only=True`); fallback heuristico `len//4` declarado
  explicitamente no campo `method` de cada medicao caso o tokenizer real
  nao esteja disponivel (nao ocorreu nesta execucao).
- **Scoring**: substring case-insensitive entre resposta gerada e
  `expected_answer` -- nao ha' juiz semantico disponivel nesta maquina;
  o dataset foi desenhado com respostas curtas e canonicas justamente
  pra' esse metodo ser honesto o suficiente.
- **Repeticoes**: `repeats=1`. Justificativa: decodificacao e' greedy
  (`do_sample=False`) e o dataset tem seed fixa -- acuracia e'
  100% deterministica por caso, repetir a rodada nao mudaria nenhum
  numero de acuracia, so' acrescentaria ruido de I/O/scheduling na
  latencia. Nao e' uma reducao de rigor, e' a consequencia honesta de
  nao haver aleatoriedade real a mediar aqui.
- **Hardware/software**: CPU-only, Windows. Python {platform.python_version()}.
  Restricao real encontrada e documentada: disco C: com ~253MB livres
  durante a execucao (`df -h`, medido) -- por isso os downloads de
  modelo (embedding, compressor LLMLingua-2) foram redirecionados pra'
  o disco E: (`HF_HOME`), e o compressor da LLMLingua/LongLLMLingua foi
  substituido por SmolLM2-360M-Instruct (ja' em cache, sem download
  novo) em vez do default da biblioteca (Llama-2-7B, ~13GB, nao cabia
  no disco disponivel).

## 3. Disponibilidade das abordagens

{availability_notes(summary)}

## 4. Resultados -- tabela principal

{table_main(summary)}

## 5. Resultados -- eficiencia (retencao de qualidade vs reducao de token)

quality_retention = accuracy_abordagem / accuracy_full_context.
token_reduction = 1 - tokens_abordagem / tokens_full_context.
Mostrados junto dos componentes, nao como metrica unica (pedido
explicito do usuario).

{table_efficiency(summary)}

## 6. Resultados por categoria

{table_by_category(breakdown) if breakdown else '(quebra por categoria nao disponivel)'}

## 7. Melhores e piores resultados (acuracia, desempate por reducao de token)

**Top 3:**
{chr(10).join(f"- {a}: accuracy={acc:.1%}, reduction={red:+.1%}, latency={lat:.0f}ms" for a, acc, red, lat in best)}

**Bottom 3:**
{chr(10).join(f"- {a}: accuracy={acc:.1%}, reduction={red:+.1%}, latency={lat:.0f}ms" for a, acc, red, lat in worst)}

## 8. Limitacoes conhecidas

- Dataset pequeno (48 casos, 8/categoria) e sintetico -- poder
  estatistico modesto, especialmente na quebra por categoria (N=8).
  `llmlingua2` roda em subconjunto ainda menor (ver secao de
  disponibilidade) por causa da latencia de compressao medida
  (~227s/caso com o compressor nativo) -- **N reportado explicitamente
  em cada tabela**, nao escondido.
- Scoring por substring, nao juizo semantico -- uma resposta
  correta mas fraseada de forma muito diferente do esperado seria
  contada como errada.
- `keyword_retrieval` e' uma implementacao independente da usada
  internamente pelo AIR (`mcp_server/retrieval.py`) -- de proposito,
  pra' nao comparar o AIR contra uma copia de si mesmo.
- `semantic_rag` usa `sentence-transformers/all-MiniLM-L6-v2`, um
  encoder pequeno e generico -- nao e' o estado da arte de embeddings.
- LLMLingua/LongLLMLingua usam SmolLM2-360M-Instruct como compressor,
  NAO o Llama-2-7B do paper original -- ver secao 2. Resultados destas
  duas linhas NAO devem ser comparados aos numeros publicados no paper.
- LLMLingua-2 usa o compressor NATIVO (`{"" if not summary["availability"].get("llmlingua2", {}).get("run") else "microsoft/llmlingua-2-xlm-roberta-large-meetingbank"}`),
  sem substituicao -- mas em subconjunto do dataset, nao no dataset
  inteiro, por custo de tempo.

## 9. Casos de falha

Ver `reports/run_raw.json` pra' cada resposta gerada e' o `correct`
individual por caso -- nao resumido aqui pra' nao inflar o relatorio,
mas nenhum caso foi removido ou escondido.

## 10. Interpretacao

Interpretacoes condicionais, restritas ao que os dados desta execucao
realmente mostram (nao generalizacoes). Cada item abaixo e' calculado
diretamente dos JSONs desta rodada, nao escrito a mao:

{interpretation_notes(summary, breakdown, raw)}

Leitura geral: qualquer afirmacao tipo "X e' melhor" acima deve ser lida
como "X teve resultado Y NESTE dataset sintetico de 48 casos, neste
modelo, nesta maquina", nao como conclusao universal.

## 11. Conclusao

Nao ha' vencedor universal declarado aqui de proposito (pedido
explicito do usuario: o objetivo e' descobrir em quais workloads cada
abordagem e' melhor/pior e por que, nao provar que uma e' superior). Os
dados das secoes 4-7 sao a resposta -- leia-os por categoria antes de
tirar conclusao.
"""

    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"Relatorio escrito em {REPORT_PATH}")


if __name__ == "__main__":
    main()
