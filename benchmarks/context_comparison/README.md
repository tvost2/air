# Context Comparison Benchmark

Comparação rigorosa e reproduzível entre o AIR e abordagens existentes
de redução/gerenciamento de contexto: Full Context, Truncation, Keyword
Retrieval, Semantic RAG, AIR, AIR + Structural Memory, LLMLingua,
LongLLMLingua, LLMLingua-2.

**Separado dos benchmarks históricos do AIR** (`../token_benchmark.py`)
e do struct-reasoning — nenhum dos dois foi alterado por este trabalho.
Ver `reports/BEFORE_*` / `reports/AFTER_*` para prova de que a suíte de
testes do AIR não regrediu.

## Estrutura

```
datasets/    dataset.json (48 casos, 6 categorias) + synthetic.py (gerador determinístico, seed fixa)
adapters/    uma implementação por abordagem (full_context, truncation, keyword_retrieval,
             semantic_rag, air_adapter, llmlingua_family) + shared_model.py (modelo final único)
metrics/     token accounting real (reaproveita mcp_server/tokens.py), scoring, agregação, efficiency_metrics
runners/     run.py (orquestrador), generate_report.py, demo.py
configs/     (reservado — configuração hoje é via CLI flags e env vars, não há YAML/JSON de config ainda)
reports/     saída de cada execução (run_raw.json, run_summary.json, run_by_category.json) + snapshots BEFORE/AFTER
report.md    relatório final (gerado por generate_report.py)
```

## Reproduzir

```
cd air
python benchmarks/context_comparison/datasets/synthetic.py       # regenera dataset.json (determinístico, seed fixa — resultado idêntico)
python benchmarks/context_comparison/runners/run.py --offline    # roda todas as abordagens locais viáveis
python benchmarks/context_comparison/runners/generate_report.py  # escreve report.md a partir dos JSONs
python benchmarks/context_comparison/runners/demo.py [case_id]   # demo Full Context vs AIR vs Semantic RAG num caso
```

Opções do runner:

```
--offline                    roda só abordagens locais (default se --provider não for passado)
--provider anthropic         tenta o caminho online; sem ANTHROPIC_API_KEY no ambiente, marca NOT RUN com o motivo — nunca inventa número
--repeats N                  repete o dataset inteiro N vezes (default 1 — ver justificativa no report.md, seção 2: decodificação greedy + seed fixa tornam acurácia determinística)
--llmlingua2-subset N        casos por categoria para o LLMLingua-2 nativo (default 2 = 12 casos; ver motivo no report.md)
```

## Restrição real de ambiente encontrada (documentada, não escondida)

Esta máquina tinha ~253MB livres no disco C: durante a execução deste
benchmark (`df -h`, medido, não estimado). Isso bloqueia downloads de
modelo direto no cache padrão do Hugging Face. Solução: `HF_HOME`
redirecionado para o disco E: (~49GB livres) — configurado por padrão em
`adapters/shared_model.py` via `os.environ.setdefault`, então não
sobrescreve se o usuário já tiver `HF_HOME` configurado. O modelo
principal (SmolLM2-360M-Instruct, já em cache local de trabalho anterior
desta sessão) foi copiado uma vez para o cache em E: para evitar
re-download.

Consequência direta nas escolhas de configuração do LLMLingua/
LongLLMLingua: o compressor **default** da biblioteca (`llmlingua`) é
`NousResearch/Llama-2-7b-hf` (~13GB, `device_map='cuda'`) — não cabia no
disco disponível nem seria viável em CPU em tempo de benchmark. Estas
duas abordagens rodam com `HuggingFaceTB/SmolLM2-360M-Instruct`
substituído como compressor (mesmo modelo usado no resto do benchmark,
já em cache) — **decisão disclosed explicitamente em cada resultado**
(campo `compressor_model_is_paper_default: false`). LLMLingua-2 usa seu
compressor nativo (`microsoft/llmlingua-2-xlm-roberta-large-meetingbank`,
baixado para E: durante este trabalho), sem substituição — mas roda num
subconjunto do dataset por causa da latência medida (~227s/caso).

## Honestidade metodológica (resumo — detalhes completos em `report.md`)

- Scoring é substring case-insensitive, não juízo semântico — o dataset
  foi desenhado com respostas curtas e canônicas para isso ser honesto
  o bastante.
- `keyword_retrieval` é uma implementação independente da usada
  internamente pelo AIR (`../../mcp_server/retrieval.py`) — de propósito,
  para não comparar o AIR contra uma cópia reduzida de si mesmo.
- Toda abordagem que não pôde rodar aparece como `NOT RUN` com o motivo
  registrado (`run_summary.json` → `availability`), nunca omitida ou
  simulada.
- Resultados de LLMLingua/LongLLMLingua com compressor substituído
  **não devem ser comparados aos números publicados no paper original**.
