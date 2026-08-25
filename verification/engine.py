"""
AIR verification -- decide se uma acao teve sucesso SEMANTICO, nao so'
mecanico.

Achado real da pesquisa (docs/ECOSYSTEM_RESEARCH.md secao 2.3): Temporal
resolve retry/checkpoint mecanico mas nao decide se a acao funcionou;
deteccao de falha por confianca do proprio modelo teve desempenho proximo
de aleatorio (ACL 2026); nenhum dos 12 frameworks avaliados garante
semantica exactly-once na fronteira de tool call. Isso e' lacuna real,
construida aqui.

Estrategia MVP, honesta sobre o que da' pra garantir: verificador
default e' heuristico (erro explicito = FAILED, output vazio = UNKNOWN,
resto = OK) -- e' um palpite fraco de proposito. O valor real vem de
registrar verificadores especificos por tool que checam efeito
observavel de verdade (ex: "arquivo existe agora?", "world state mudou
como esperado?") em vez de confiar no texto que o LLM devolveu sobre si
mesmo -- e' exatamente o padrao Reason-Act-VERIFY-Observe (Co-ReAct) que
a pesquisa identificou como nome certo pra isso, sem lib pronta.
"""
from __future__ import annotations

from typing import Callable

from core.types import ActionResult, Verification, VerificationOutcome, new_id

Verifier = Callable[[ActionResult], "tuple[VerificationOutcome, str]"]


def default_heuristic_verifier(result: ActionResult) -> tuple[VerificationOutcome, str]:
    if result.error:
        return VerificationOutcome.FAILED, f"erro reportado pela acao: {result.error}"
    if result.output is None or result.output == "":
        return VerificationOutcome.UNKNOWN, "acao nao reportou erro, mas tambem nao produziu output verificavel"
    return VerificationOutcome.OK, "sem erro reportado e output presente (heuristica fraca -- registre um verificador especifico pra garantia real)"


class VerificationEngine:
    def __init__(self):
        self._verifiers: dict[str, Verifier] = {}

    def register(self, tool_name: str, verifier: Verifier) -> None:
        """Verificador especifico por tool -- checa efeito observavel de
        verdade (ex: consultar world state), nao so' o texto de retorno."""
        self._verifiers[tool_name] = verifier

    def verify(self, result: ActionResult) -> Verification:
        verifier = self._verifiers.get(result.tool_name, default_heuristic_verifier)
        outcome, detail = verifier(result)
        return Verification(id=new_id("ver"), action_result_id=result.id, outcome=outcome, detail=detail)
