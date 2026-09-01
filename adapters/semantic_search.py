"""
AIR adapters -- primeiro conteudo real de adapters/ (antes vazio,
documentado no README como lacuna: "adapters/ ainda esta vazio"). Busca
semantica via sentence-transformers -- adota biblioteca madura em vez de
reimplementar embeddings do zero, mesma regra de "adotar, nao construir"
de docs/ECOSYSTEM_RESEARCH.md (vector DB/embeddings ja' estava listado
la' como peca madura, nao lacuna a construir).

DESLIGADO POR PADRAO -- so' liga com AIR_ENABLE_SEMANTIC_SEARCH=true.
Motivo medido, nao hipotetico: nesta maquina, so' o
`from sentence_transformers import SentenceTransformer` levou **187
segundos** (mesma patologia de I/O de disco ja' documentada pro
tokenizer em mcp_server/tokens.py -- 208s no primeiro load do
AutoTokenizer, ver README secao "Limitacoes conhecidas"). Se isso
rodasse por padrao no startup do mcp_server, reproduziria (ou pioraria:
sentence-transformers + torch e' pilha de dependencia bem maior que so'
o tokenizer) o mesmo risco de connection timeout que motivou
tokens.warm_tokenizer_async() em primeiro lugar. Por isso aqui:

1. O import pesado (`sentence_transformers`) e' LAZY -- so' acontece
   dentro de _get_model(), nunca no topo deste modulo. Importar este
   modulo (`import adapters.semantic_search`) e' instantaneo; so' chamar
   embed()/_get_model() de verdade paga o custo.
2. Nao ha' warmup automatico em background tipo tokens.py -- dado o custo
   medido (187s so' de import, antes mesmo do modelo em si), rodar isso
   numa thread de fundo sem o usuario pedir seria gastar CPU/memoria da
   maquina do usuario por uma feature que ele nao ligou. Quem liga
   AIR_ENABLE_SEMANTIC_SEARCH=true paga o custo explicitamente, sabendo
   (README documenta o numero medido).
3. Falha graciosa: se o pacote nao estiver instalado, ou o load falhar
   por qualquer motivo (sem rede, disco cheio, etc.), embed() devolve
   None -- mcp_server/retrieval.py trata isso como "sem busca semantica
   disponivel agora", cai pra busca por palavra-chave sozinha (o
   comportamento de sempre), nunca quebra a busca inteira por causa de
   uma feature opcional.

Custo medido nesta maquina, uma vez, com modelo ja' em cache local
depois disso (sentence-transformers/all-MiniLM-L6-v2, ~90MB): import
~187s, load do modelo (download + init) ~27s, encode() de 2 frases
curtas ~6.8s. Cosine similarity medida entre um par de frases parafraseadas
SEM nenhuma palavra em comum ("air runtime tem cache redis em producao"
vs "qual banco de dados o servico de cache usa"): 0.443 -- e' a base de
calibracao de SEMANTIC_MATCH_THRESHOLD abaixo, nao um numero inventado.
Em maquina sem essa patologia de I/O de disco os tempos serao bem
menores, mas o principio -- nunca afirmar rapido sem medir -- vale
igual.
"""
from __future__ import annotations

import os

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Calibrado contra o par parafraseado medido no docstring acima (0.443)
# -- fica um pouco abaixo pra' nao exigir uma parafrase quase perfeita,
# mas bem acima de 0 pra' nao inundar resultado com ruido. Heuristica
# declarada como tal, nao afirmada como valor "correto" universal --
# nenhum dataset de relevancia foi usado pra' otimizar isso, e' um
# ponto de partida razoavel a partir de UMA medicao real.
SEMANTIC_MATCH_THRESHOLD = 0.35

_model = None
_model_name: str | None = None
_load_failed = False


def enabled() -> bool:
    return os.environ.get("AIR_ENABLE_SEMANTIC_SEARCH", "").strip().lower() in ("1", "true", "yes")


def model_name() -> str | None:
    """Nome do modelo realmente carregado, ou None se nunca carregou (ou
    falhou). Usado pelo campo 'method' do retorno de busca -- honestidade
    sobre qual modelo gerou o resultado, mesmo padrao de
    mcp_server/tokens.py."""
    return _model_name


def _get_model():
    """Lazy de proposito -- ver docstring do modulo. So' importa
    sentence_transformers na PRIMEIRA chamada real."""
    global _model, _model_name, _load_failed
    if _model is not None or _load_failed:
        return _model
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(DEFAULT_MODEL)
        _model_name = DEFAULT_MODEL
    except Exception:
        _load_failed = True
        _model = None
    return _model


def embed(texts: list[str]):
    """Devolve array numpy (um vetor por texto) ou None se o modelo nao
    estiver disponivel (pacote ausente, load falhou, ou lista vazia)."""
    if not texts:
        return None
    model = _get_model()
    if model is None:
        return None
    return model.encode(list(texts), convert_to_numpy=True)


def cosine_similarity(a, b) -> float:
    import numpy as np
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


class SemanticIndex:
    """Cache de embeddings por (kind, record_id), invalidado por versao
    -- mesmo padrao de mcp_server/kakeya_index.py:KakeyaContextIndex
    (rebuild so' quando WorldState.version()/MemoryStore.version() mudou
    desde a ultima vez). Precomputar embeddings uma vez por registro
    (nao a cada busca) e' o que torna buscar por eles barato: a busca em
    si so' embeda a QUERY (uma chamada) e faz produto escalar contra os
    vetores ja' prontos -- reembedar todo o corpus a cada busca seria
    proibitivo."""

    def __init__(self) -> None:
        self._vectors: dict[str, dict[str, object]] = {"fact": {}, "entity": {}, "event": {}}
        self._built_at_version: tuple[int, int] | None = None

    def ensure_fresh(self, world_version: int, memory_version: int, builders: dict) -> None:
        current = (world_version, memory_version)
        if self._built_at_version == current:
            return
        for kind, builder in builders.items():
            records = builder()
            ids = [r[0] for r in records]
            texts = [r[1] for r in records]
            vectors = embed(texts) if texts else None
            self._vectors[kind] = dict(zip(ids, vectors)) if vectors is not None else {}
        self._built_at_version = current

    def similarity_scores(self, kind: str, query_vector) -> dict[str, float]:
        """id -> cosine similarity contra a query, pra' TODO registro
        indexado dessa dimensao (nao so' os que ja' bateram por
        palavra-chave -- e' o ponto da busca semantica: achar o que
        keyword nunca acharia)."""
        if query_vector is None:
            return {}
        return {rid: cosine_similarity(query_vector, vec) for rid, vec in self._vectors[kind].items()}
