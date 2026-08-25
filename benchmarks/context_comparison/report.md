# Context Comparison Benchmark -- Relatorio

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
- **Modelo final**: `HuggingFaceTB/SmolLM2-360M-Instruct`
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
- **Hardware/software**: CPU-only, Windows. Python 3.11.9.
  Restricao real encontrada e documentada: disco C: com ~253MB livres
  durante a execucao (`df -h`, medido) -- por isso os downloads de
  modelo (embedding, compressor LLMLingua-2) foram redirecionados pra'
  o disco E: (`HF_HOME`), e o compressor da LLMLingua/LongLLMLingua foi
  substituido por SmolLM2-360M-Instruct (ja' em cache, sem download
  novo) em vez do default da biblioteca (Llama-2-7B, ~13GB, nao cabia
  no disco disponivel).

## 3. Disponibilidade das abordagens

(todas as abordagens planejadas rodaram)

## 4. Resultados -- tabela principal

| Approach | Input Tokens (media) | Reduction | Accuracy | Retrieval Latency (ms) | Total Latency (ms) | N |
|---|---|---|---|---|---|---|
| full_context | 322.9 | +0.0% | +50.0% | N/A | 9065.3 | 48 |
| truncation_200 | 147.7 | +30.6% | +41.7% | N/A | 6933.0 | 48 |
| truncation_500 | 195.1 | +13.4% | +50.0% | N/A | 7385.2 | 48 |
| truncation_1000 | 222.9 | +10.0% | +50.0% | N/A | 7648.5 | 48 |
| keyword_retrieval | 145.1 | +32.1% | +58.3% | N/A | 6984.7 | 48 |
| semantic_rag | 146.9 | +30.9% | +70.8% | N/A | 7216.1 | 48 |
| air | 478.7 | -82.4% | +14.6% | N/A | 10404.5 | 48 |
| air_structural_memory | 479.9 | -83.2% | +37.5% | N/A | 10489.9 | 48 |
| llmlingua | 329.9 | -3.7% | +41.7% | N/A | 9735.5 | 48 |
| longllmlingua | 330.2 | -4.3% | +45.8% | N/A | 12352.5 | 48 |
| llmlingua2 | 187.8 | +33.9% | +33.3% | N/A | 53891.9 | 12 |

## 5. Resultados -- eficiencia (retencao de qualidade vs reducao de token)

quality_retention = accuracy_abordagem / accuracy_full_context.
token_reduction = 1 - tokens_abordagem / tokens_full_context.
Mostrados junto dos componentes, nao como metrica unica (pedido
explicito do usuario).

| Approach | Accuracy Retention | Token Reduction | Latency Overhead (ms) |
|---|---|---|---|
| full_context | +100.0% | +0.0% | 0.0 |
| truncation_200 | +83.3% | +54.3% | -2132.3 |
| truncation_500 | +100.0% | +39.6% | -1680.1 |
| truncation_1000 | +100.0% | +31.0% | -1416.7 |
| keyword_retrieval | +116.7% | +55.1% | -2080.6 |
| semantic_rag | +141.7% | +54.5% | -1849.2 |
| air | +29.2% | -48.3% | 1339.2 |
| air_structural_memory | +75.0% | -48.6% | 1424.7 |
| llmlingua | +83.3% | -2.2% | 670.3 |
| longllmlingua | +91.7% | -2.3% | 3287.2 |
| llmlingua2 | +66.7% | +41.8% | 44826.6 |

## 6. Resultados por categoria

### factual (lookup direto)

| Approach | Accuracy | Input Tokens (media) | Reduction | N |
|---|---|---|---|---|
| full_context | +62.5% | 160.4 | +0.0% | 8 |
| truncation_200 | +62.5% | 143.2 | +10.0% | 8 |
| truncation_500 | +62.5% | 160.4 | +0.0% | 8 |
| truncation_1000 | +62.5% | 160.4 | +0.0% | 8 |
| keyword_retrieval | +100.0% | 129.9 | +19.0% | 8 |
| semantic_rag | +100.0% | 142.9 | +10.1% | 8 |
| air | +25.0% | 356.5 | -121.1% | 8 |
| air_structural_memory | +37.5% | 358.0 | -122.0% | 8 |
| llmlingua | +37.5% | 157.9 | +0.7% | 8 |
| longllmlingua | +62.5% | 164.9 | -3.0% | 8 |
| llmlingua2 | +50.0% | 113.0 | +29.1% | 2 |

### irrelevant context (distratores)

| Approach | Accuracy | Input Tokens (media) | Reduction | N |
|---|---|---|---|---|
| full_context | +50.0% | 182.6 | +0.0% | 8 |
| truncation_200 | +62.5% | 142.4 | +22.0% | 8 |
| truncation_500 | +50.0% | 182.6 | +0.0% | 8 |
| truncation_1000 | +50.0% | 182.6 | +0.0% | 8 |
| keyword_retrieval | +62.5% | 142.5 | +22.0% | 8 |
| semantic_rag | +50.0% | 142.1 | +22.2% | 8 |
| air | +12.5% | 415.6 | -127.6% | 8 |
| air_structural_memory | +62.5% | 416.5 | -128.1% | 8 |
| llmlingua | +50.0% | 195.6 | -7.1% | 8 |
| longllmlingua | +50.0% | 195.6 | -7.1% | 8 |
| llmlingua2 | +0.0% | 126.5 | +30.7% | 2 |

### long-distance ("lost in the middle")

| Approach | Accuracy | Input Tokens (media) | Reduction | N |
|---|---|---|---|---|
| full_context | +37.5% | 988.5 | +0.0% | 8 |
| truncation_200 | +12.5% | 143.6 | +85.3% | 8 |
| truncation_500 | +37.5% | 233.0 | +76.2% | 8 |
| truncation_1000 | +37.5% | 388.8 | +60.3% | 8 |
| keyword_retrieval | +50.0% | 140.2 | +85.7% | 8 |
| semantic_rag | +75.0% | 141.8 | +85.5% | 8 |
| air | +25.0% | 1015.6 | -3.9% | 8 |
| air_structural_memory | +62.5% | 1013.0 | -3.5% | 8 |
| llmlingua | +0.0% | 988.5 | +0.0% | 8 |
| longllmlingua | +0.0% | 988.5 | +0.0% | 8 |
| llmlingua2 | +0.0% | 482.0 | +51.6% | 2 |

### multi-hop (combinar 2 fatos)

| Approach | Accuracy | Input Tokens (media) | Reduction | N |
|---|---|---|---|---|
| full_context | +50.0% | 185.9 | +0.0% | 8 |
| truncation_200 | +12.5% | 154.8 | +15.9% | 8 |
| truncation_500 | +50.0% | 185.9 | +0.0% | 8 |
| truncation_1000 | +50.0% | 185.9 | +0.0% | 8 |
| keyword_retrieval | +0.0% | 147.0 | +20.0% | 8 |
| semantic_rag | +62.5% | 144.4 | +21.4% | 8 |
| air | +0.0% | 432.5 | -131.6% | 8 |
| air_structural_memory | +0.0% | 435.4 | -133.2% | 8 |
| llmlingua | +25.0% | 196.2 | -5.7% | 8 |
| longllmlingua | +25.0% | 204.0 | -9.7% | 8 |
| llmlingua2 | +0.0% | 125.5 | +28.8% | 2 |

### recency/conflict

| Approach | Accuracy | Input Tokens (media) | Reduction | N |
|---|---|---|---|---|
| full_context | +12.5% | 178.8 | +0.0% | 8 |
| truncation_200 | +12.5% | 159.4 | +10.0% | 8 |
| truncation_500 | +12.5% | 178.8 | +0.0% | 8 |
| truncation_1000 | +12.5% | 178.8 | +0.0% | 8 |
| keyword_retrieval | +37.5% | 163.8 | +7.6% | 8 |
| semantic_rag | +37.5% | 163.2 | +7.9% | 8 |
| air | +0.0% | 287.4 | -59.2% | 8 |
| air_structural_memory | +0.0% | 287.9 | -59.5% | 8 |
| llmlingua | +50.0% | 188.1 | -5.4% | 8 |
| longllmlingua | +62.5% | 191.6 | -7.0% | 8 |
| llmlingua2 | +50.0% | 137.0 | +25.5% | 2 |

### repeated information (redundancia)

| Approach | Accuracy | Input Tokens (media) | Reduction | N |
|---|---|---|---|---|
| full_context | +87.5% | 241.1 | +0.0% | 8 |
| truncation_200 | +87.5% | 142.9 | +40.2% | 8 |
| truncation_500 | +87.5% | 229.9 | +4.2% | 8 |
| truncation_1000 | +87.5% | 241.1 | +0.0% | 8 |
| keyword_retrieval | +100.0% | 147.2 | +38.3% | 8 |
| semantic_rag | +100.0% | 147.2 | +38.3% | 8 |
| air | +25.0% | 364.5 | -51.4% | 8 |
| air_structural_memory | +62.5% | 368.4 | -53.0% | 8 |
| llmlingua | +87.5% | 252.8 | -4.9% | 8 |
| longllmlingua | +75.0% | 236.9 | +0.7% | 8 |
| llmlingua2 | +100.0% | 143.0 | +37.7% | 2 |

## 7. Melhores e piores resultados (acuracia, desempate por reducao de token)

**Top 3:**
- semantic_rag: accuracy=70.8%, reduction=+30.9%, latency=7216ms
- keyword_retrieval: accuracy=58.3%, reduction=+32.1%, latency=6985ms
- truncation_500: accuracy=50.0%, reduction=+13.4%, latency=7385ms

**Bottom 3:**
- air: accuracy=14.6%, reduction=-82.4%, latency=10404ms
- llmlingua2: accuracy=33.3%, reduction=+33.9%, latency=53892ms
- air_structural_memory: accuracy=37.5%, reduction=-83.2%, latency=10490ms

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
- LLMLingua-2 usa o compressor NATIVO (`microsoft/llmlingua-2-xlm-roberta-large-meetingbank`),
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

- Na categoria **factual (lookup direto)**, `keyword_retrieval` teve a maior acuracia desta rodada (100.0%, N=8).
- Na categoria **irrelevant context (distratores)**, `truncation_200` teve a maior acuracia desta rodada (62.5%, N=8).
- Na categoria **long-distance ("lost in the middle")**, `semantic_rag` teve a maior acuracia desta rodada (75.0%, N=8).
- Na categoria **multi-hop (combinar 2 fatos)**, `semantic_rag` teve a maior acuracia desta rodada (62.5%, N=8).
- Na categoria **recency/conflict**, `longllmlingua` teve a maior acuracia desta rodada (62.5%, N=8).
- Na categoria **repeated information (redundancia)**, `keyword_retrieval` teve a maior acuracia desta rodada (100.0%, N=8).
- `air` enviou **mais** tokens que o full_context nesta rodada (-82.4% de 'reducao', ou seja, aumento). 
- `air_structural_memory` enviou **mais** tokens que o full_context nesta rodada (-83.2% de 'reducao', ou seja, aumento). 
- `llmlingua` enviou **mais** tokens que o full_context nesta rodada (-3.7% de 'reducao', ou seja, aumento). 
- `longllmlingua` enviou **mais** tokens que o full_context nesta rodada (-4.3% de 'reducao', ou seja, aumento). 
- **Por que AIR/AIR+Structural Memory aumentam tokens neste benchmark**: `air/adapter.py::get_context` (codigo real do AIR, nao modificado pro benchmark) busca ate' 20 candidatos por palavra-chave sem limiar minimo de relevancia (so' `score > 0`) -- em casos com poucas sentencas no total, isso retorna quase TUDO que foi ingerido, e cada item retorna envolto em notacao `FACT(...)` + handle/label, que e' mais verboso por sentenca que o texto bruto. O mecanismo de referencia-por-ID do AIR (validado no benchmark de token do proprio AIR, `../token_benchmark.py`) economiza quando o MESMO conteudo seria reenviado REPETIDAMENTE ao longo de varios turnos -- este benchmark testa UMA UNICA consulta por caso, entao essa vantagem especifica nao tem chance de aparecer aqui. E' uma limitacao real do design atual de retrieval do AIR pra' este tipo de workload (single-shot, muitos fatos pequenos ingeridos), nao um erro de medicao.
- `llmlingua` teve **0% de acuracia** na categoria long-distance ("lost in the middle") nesta rodada (compressor substituido, ver limitacoes).
- `longllmlingua` teve **0% de acuracia** na categoria long-distance ("lost in the middle") nesta rodada (compressor substituido, ver limitacoes).

Leitura geral: qualquer afirmacao tipo "X e' melhor" acima deve ser lida
como "X teve resultado Y NESTE dataset sintetico de 48 casos, neste
modelo, nesta maquina", nao como conclusao universal.

## 11. Conclusao

Nao ha' vencedor universal declarado aqui de proposito (pedido
explicito do usuario: o objetivo e' descobrir em quais workloads cada
abordagem e' melhor/pior e por que, nao provar que uma e' superior). Os
dados das secoes 4-7 sao a resposta -- leia-os por categoria antes de
tirar conclusao.
