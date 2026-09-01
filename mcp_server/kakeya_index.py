"""
AIR mcp_server -- indice de convergencia por bissecao pra busca por
substring, batizado "Kakeya" por ANALOGIA deliberada, nao implementacao
literal do problema.

O problema da agulha de Kakeya (Besicovitch/Perron, comeco do sec. XX) e'
sobre cobrir TODAS as rotacoes possiveis de uma agulha de comprimento 1
com AREA/VOLUME minimo -- resultado surpreendente: pode ser feito com
medida arbitrariamente pequena, via particao recursiva do plano/espaco em
fatias finas (Perron tree) que se sobrepoem de jeito nao-obvio. E' teoria
da medida geometrica, nao existe "algoritmo Kakeya" de busca -- afirmar
isso seria inventar matematica que nao existe.

O que reaproveita de verdade e' o PRINCIPIO por tras da construcao de
Perron tree: nao varrer o espaco inteiro pra achar o alvo, convergir por
BISSECAO repetida "no meio" de uma estrutura particionada. Aplicado ao
problema real que mcp_server/retrieval.py tem (achar todo registro que
contem uma substring arbitraria, sem trocar essa semantica por algo mais
fraco tipo match de palavra exata): array de sufixos ordenado + busca
binaria (modulo `bisect`, stdlib) -- estrutura padrao e' comprovada pra
"quais registros contem esta substring, em qualquer posicao" em
O(log n + k) por palavra de busca em vez de O(n), onde n = total de
sufixos indexados e k = numero de resultados. Nao e' estrutura inventada
aqui, so' e' aplicada ao dominio do AIR.

"3D": tres indices independentes, um por DIMENSAO REAL do dado buscavel no
AIR -- fact, entity, event. Nao decorativo: sao literalmente as tres
fontes que mcp_server/retrieval.py:search_world()/search_facts() ja'
varria linearmente antes desta mudanca.

Trade-off disclosed, nao escondido (mesma disciplina do resto do
projeto): cada registro so' tem os primeiros KAKEYA_MAX_INDEXED_CHARS
caracteres do seu texto buscavel indexados. Para o conteudo tipico do AIR
(fato "subject predicate = obj", entidade "kind name attrs") isso cobre
tudo com folga -- mas um `air_store_memory` com conteudo bem mais longo
que isso (o limite de content e' 20_000 caracteres, ver
mcp_server/config.py) pode ter uma ocorrencia de substring so' na parte
nao indexada. Por isso NUNCA se apoia so' no indice: ele so' reduz quais
registros precisam ser reescaneados por _score (mcp_server/retrieval.py),
que continua sendo a fonte de verdade da pontuacao -- o indice erra pra'
mais (pode incluir candidato que depois pontua 0, sem problema), nunca
pra' menos DENTRO do trecho indexado.
"""
from __future__ import annotations

import bisect
from typing import Callable

KAKEYA_MAX_INDEXED_CHARS = 4000


class SuffixBisectIndex:
    """Array ordenado de (sufixo_em_minusculas, record_id). Toda substring
    de um texto e' PREFIXO de algum sufixo dele -- "record contem a
    substring W" equivale a "existe, no array ordenado, um sufixo que
    comeca com W". Sufixos que comecam com W formam um intervalo CONTIGUO
    na ordem lexicografica (mesma logica de qualquer estrutura ordenada
    por prefixo, ex: indice de dicionario) -- dois bisect (bordas inferior
    e superior desse intervalo) acham o intervalo inteiro em O(log n), sem
    varrer o array."""

    def __init__(self) -> None:
        self._suffixes: list[str] = []  # ordenado
        self._ids: list[str] = []       # mesmo indice de _suffixes
        self._overflow_ids: set[str] = set()  # ver overflow_ids()
        self._built = False

    def build(self, records: list[tuple[str, str]]) -> None:
        """records: lista de (record_id, texto_buscavel). Reconstroi do
        zero -- chamado so' quando a versao do storage mudou desde a
        ultima busca (ver KakeyaContextIndex.ensure_fresh), nao a cada
        chamada de busca."""
        pairs: list[tuple[str, str]] = []
        overflow: set[str] = set()
        for record_id, text in records:
            full_low = text.lower()
            if len(full_low) > KAKEYA_MAX_INDEXED_CHARS:
                # texto maior que o trecho indexado: um match pode existir
                # so' na parte nao coberta. Em vez de arriscar um falso
                # negativo (a mesma coisa que a semantica atual de
                # _score() NUNCA faz, porque ela varre o texto inteiro),
                # este record entra em overflow_ids() -- retrieval.py trata
                # todo overflow como candidato sempre, independente de
                # palavra, voltando pro comportamento O(n) so' pra' esses
                # poucos registros longos em vez de silenciosamente
                # restringir a busca.
                overflow.add(record_id)
            low = full_low[:KAKEYA_MAX_INDEXED_CHARS]
            for start in range(len(low)):
                pairs.append((low[start:], record_id))
        pairs.sort(key=lambda p: p[0])
        self._suffixes = [p[0] for p in pairs]
        self._ids = [p[1] for p in pairs]
        self._overflow_ids = overflow
        self._built = True

    def candidates(self, word_lower: str) -> set[str]:
        """Ids de todo record cujo trecho indexado contem word_lower como
        substring, em qualquer posicao. NAO inclui overflow_ids() -- quem
        chama precisa unir com overflow_ids() separadamente (uma vez por
        busca, nao uma vez por palavra)."""
        if not word_lower or not self._built:
            return set()
        lo = bisect.bisect_left(self._suffixes, word_lower)
        # borda superior do intervalo: primeiro sufixo que NAO tem
        # word_lower como prefixo. '\U0010FFFF' e' o maior code point
        # unicode valido -- word_lower + esse caractere e' maior, na
        # ordem lexicografica, que qualquer string que comeca com
        # word_lower (mesmo truque padrao de range query por prefixo em
        # estrutura ordenada).
        hi = bisect.bisect_right(self._suffixes, word_lower + "\U0010ffff")
        return set(self._ids[lo:hi])

    def overflow_ids(self) -> set[str]:
        """Ids cujo texto passou de KAKEYA_MAX_INDEXED_CHARS -- ficam de
        fora da cobertura garantida do bisect, entao devem ser tratados
        como candidatos sempre (rede de seguranca, ver build())."""
        return set(self._overflow_ids)


class KakeyaContextIndex:
    """Tres SuffixBisectIndex (fact/entity/event), com cache invalidado
    por numero de versao -- reconstroi so' quando algo mudou desde a
    ultima busca (WorldState.version()/MemoryStore.version(), incrementado
    em toda escrita), nao a cada chamada de air_search_context/
    air_get_context. Uma escrita em QUALQUER dimensao invalida as tres --
    granularidade grosseira de proposito (correta e simples) em vez de
    rastrear qual tabela mudou; dado o padrao de uso esperado (muito mais
    busca que escrita), reconstroes ficam raras na pratica."""

    def __init__(self) -> None:
        self._indices: dict[str, SuffixBisectIndex] = {
            "fact": SuffixBisectIndex(),
            "entity": SuffixBisectIndex(),
            "event": SuffixBisectIndex(),
        }
        self._built_at_version: tuple[int, int] | None = None

    def ensure_fresh(self, world_version: int, memory_version: int, builders: dict[str, Callable[[], list[tuple[str, str]]]]) -> None:
        """builders: uma funcao por dimensao (fact/entity/event) que
        BUSCA os registros do storage -- so' e' chamada de verdade quando
        a versao mudou desde a ultima reconstrucao. Callable em vez de
        lista pronta de proposito: se fosse lista pronta, quem chama teria
        que buscar tudo do banco ANTES de saber se ia precisar reconstruir
        ou nao, o que anularia o ganho de nao reconstruir a toa."""
        current = (world_version, memory_version)
        if self._built_at_version == current:
            return
        for kind, index in self._indices.items():
            index.build(builders[kind]())
        self._built_at_version = current

    def candidates(self, kind: str, word_lower: str) -> set[str]:
        return self._indices[kind].candidates(word_lower)

    def overflow_ids(self, kind: str) -> set[str]:
        return self._indices[kind].overflow_ids()
