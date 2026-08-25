"""
AIR context -- Context Engine: a peca central da proposta original.

Principio (dito pelo usuario, verbatim): "Informacao persistente e
estruturada NAO deve ser enviada repetidamente como tokens ao LLM."

Confirmado pela pesquisa (docs/ECOSYSTEM_RESEARCH.md secao 2.1) como
"territorio praticamente livre" -- nenhum projeto maduro ataca
especificamente "referenciar objeto grande por ID em vez de serializar
tudo" como abstracao reutilizavel. E' a lacuna real, entao e' construida
aqui de verdade, nao adaptada de outro projeto.

Mecanica: todo output grande (resultado de tool, query no world state,
arquivo lido, fato recuperado da memoria) entra no ContextEngine via
put() e ganha um handle curto. O que vai pro prompt do LLM e' o handle +
um resumo compacto, NAO o conteudo inteiro -- igual voce nao reenvia um
arquivo de 10 mil linhas toda vez que fala dele, so' referencia o nome.
O LLM (ou o codigo do agente) so' busca o conteudo completo via get(id)
quando realmente precisa operar em cima dele.

Isso e' diferente de compressao de texto (LLMLingua etc, que a pesquisa
descartou por "generalization gap") -- aqui nao se comprime o conteudo,
se EVITA reenviar o que ja foi visto, por referencia estrutural.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.types import new_id, now

# abaixo deste tamanho, o conteudo e' inlinado direto no contexto -- o
# overhead de um handle (id + resumo) nao compensa pra coisa pequena.
INLINE_THRESHOLD_CHARS = 200
DEFAULT_SUMMARY_CHARS = 120


@dataclass
class ContextItem:
    id: str
    kind: str          # ex: "tool_output", "file", "world_query", "fact_set"
    label: str          # descricao curta legivel, ex: "resultado de ls /var/log"
    content: str
    size_chars: int
    created_at: float = field(default_factory=now)
    pinned: bool = False   # pinned nunca vira so' referencia, sempre entra inteiro (ex: instrucao do sistema)


class ContextEngine:
    def __init__(self):
        self._items: dict[str, ContextItem] = {}

    def put(self, content: str, kind: str, label: str, pinned: bool = False) -> str:
        item = ContextItem(id=new_id("ctx"), kind=kind, label=label, content=content, size_chars=len(content), pinned=pinned)
        self._items[item.id] = item
        return item.id

    def get(self, handle_id: str) -> str:
        """Busca o conteudo completo -- so' chamado quando o agente
        realmente precisa operar sobre o dado, nao a cada turno."""
        item = self._items.get(handle_id)
        if item is None:
            raise KeyError(f"handle desconhecido: {handle_id}")
        return item.content

    def item(self, handle_id: str) -> ContextItem | None:
        return self._items.get(handle_id)

    def _summarize(self, item: ContextItem) -> str:
        if item.size_chars <= DEFAULT_SUMMARY_CHARS:
            return item.content
        head = item.content[:DEFAULT_SUMMARY_CHARS].rsplit(" ", 1)[0]
        return f"{head}... ({item.size_chars} chars, use get('{item.id}') pra ver tudo)"

    def summarize(self, handle_id: str) -> str:
        """Versao publica de _summarize -- pra' chamador externo (ex:
        tools/registry.py) nao precisar acessar metodo privado."""
        item = self._items.get(handle_id)
        return self._summarize(item) if item else ""

    def render(self, handle_ids: list[str] | None = None) -> str:
        """Monta o texto que vai de fato pro prompt: itens pequenos ou
        pinned inteiros, itens grandes so' como referencia+resumo. Isto
        e' o que substitui reenviar tudo toda vez."""
        ids = handle_ids if handle_ids is not None else list(self._items.keys())
        lines = []
        for hid in ids:
            item = self._items.get(hid)
            if item is None:
                continue
            if item.pinned or item.size_chars <= INLINE_THRESHOLD_CHARS:
                lines.append(f"[{item.kind}:{item.id}] {item.label}\n{item.content}")
            else:
                lines.append(f"[{item.kind}:{item.id}] {item.label} -- {self._summarize(item)}")
        return "\n\n".join(lines)

    def budget_chars(self, handle_ids: list[str] | None = None) -> int:
        return len(self.render(handle_ids))

    def full_size_chars(self, handle_ids: list[str] | None = None) -> int:
        ids = handle_ids if handle_ids is not None else list(self._items.keys())
        return sum(self._items[h].size_chars for h in ids if h in self._items)
