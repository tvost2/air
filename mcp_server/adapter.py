"""
AIR mcp_server -- adapter entre o nucleo do AIR e o protocolo MCP.

Principio arquitetural (pedido explicito do usuario): "AIR core acima,
MCP adapter abaixo" -- nao o contrario. Este modulo NAO sabe nada sobre
JSON-RPC, stdio, ou o SDK 'mcp' -- so' recebe argumentos Python simples
(str, int, dict) e devolve dict serializavel. server.py e' a UNICA parte
que conhece o protocolo MCP; se algum dia o AIR precisar de outra forma
de exposicao (HTTP, CLI, outro protocolo), este adapter continua igual,
so' troca quem chama ele. O AIR core (world/, memory/, context/,
planner/, verification/) continua 100% utilizavel sem MCP, exatamente
como antes deste trabalho -- nenhuma dessas pastas foi alterada na sua
API publica, so' ganharam metodos novos de consulta (all_active,
all_entities, all_events, get_fact) que qualquer consumidor pode usar,
MCP ou nao.

Cada metodo publico devolve SEMPRE um dict (nunca levanta excecao pra'
condicao esperada -- id inexistente, memoria vazia, etc.) porque o SDK
MCP instalado nesta maquina (mcp==2.1.0) transforma excecao de dentro de
tool em erro opaco (UnexpectedToolError) que esconde a causa de quem
chamou -- um erro estruturado e legivel e' mais util pro LLM do outro
lado do MCP.
"""
from __future__ import annotations

import logging
import threading
import time

from context.engine import ContextEngine
from core.types import Fact, FactStatus, VerificationOutcome
from memory.store import MemoryStore
from planner.planner import Planner
from verification.engine import VerificationEngine
from world.state import WorldState

from mcp_server import retrieval, tokens
from mcp_server.config import Config

logger = logging.getLogger("air.mcp_server.adapter")


def _render_fact(fact: Fact, structural: bool) -> str:
    if structural:
        # mesmo principio validado em struct-reasoning/memory (notacao
        # posicional compacta bate prosa em acuracia E tokens, N=200) --
        # nao copia o codigo daquele projeto (e' um experimento separado),
        # mas reaproveita o formato que a medicao real validou.
        return f"FACT({fact.subject},{fact.predicate},{fact.obj})"
    return f"{fact.subject} tem {fact.predicate} igual a '{fact.obj}'" + (f" (motivo: {fact.reason})" if fact.reason else "")


class AirAdapter:
    def __init__(self, config: Config):
        self.config = config
        config.ensure_storage_dir()
        db_path = str(config.storage_path)
        self.world = WorldState(db_path)
        self.memory = MemoryStore(db_path)
        self.context = ContextEngine()
        self.verification = VerificationEngine()
        self.planner = Planner(self.verification)
        # trava grossa e' suficiente aqui: o SDK MCP despacha cada tool
        # call sync num worker thread (anyio.to_thread.run_sync) -- sem
        # isso, duas chamadas concorrentes poderiam intercalar
        # escritas/leituras no mesmo Planner/ContextEngine (que nao sao
        # thread-safe por si so'). Nao e' infraestrutura nova, e' o minimo
        # pra' nao corromper estado compartilhado.
        self._lock = threading.Lock()

        # verificador especifico pra' tarefas de retrieval/reconstrucao de
        # contexto: resultado VAZIO nao e' falha (busca sem match e' um
        # resultado legitimo), so' ERRO reportado e' falha de verdade.
        # Reaproveita o ponto de extensao que verification/engine.py ja'
        # oferece (verification.register), nao contorna nem duplica.
        def _no_error_is_ok(result):
            if result.error:
                return VerificationOutcome.FAILED, result.error
            return VerificationOutcome.OK, "sem erro -- resultado vazio e' outcome legitimo pra' retrieval"

        self.verification.register("mcp_retrieval", _no_error_is_ok)
        self.verification.register("mcp_structural_memory", _no_error_is_ok)
        self.verification.register("mcp_context_reconstruction", _no_error_is_ok)

    # ------------------------------------------------------------------
    # air_search_context
    # ------------------------------------------------------------------
    def search_context(self, query: str, limit: int | None = None) -> dict:
        with self._lock:
            return self._search_context(query, limit)

    def _search_context(self, query: str, limit: int | None = None) -> dict:
        query = (query or "").strip()
        if not query:
            return {"error": "query vazia"}
        if len(query) > self.config.max_query_chars:
            return {"error": f"query excede o limite de {self.config.max_query_chars} caracteres"}

        limit = limit or self.config.default_search_limit
        result = retrieval.search(self.world, self.memory, query, limit=limit)
        logger.info("search_context query_len=%d results=%d latency_ms=%.1f", len(query), len(result["results"]), result["latency_ms"])
        return result

    # ------------------------------------------------------------------
    # air_store_memory
    # ------------------------------------------------------------------
    def store_memory(self, content: str, metadata: dict | None = None) -> dict:
        with self._lock:
            return self._store_memory(content, metadata)

    def _store_memory(self, content: str, metadata: dict | None = None) -> dict:
        content = (content or "").strip()
        if not content:
            return {"error": "content vazio"}
        if len(content) > self.config.max_content_chars:
            return {"error": f"content excede o limite de {self.config.max_content_chars} caracteres"}

        metadata = metadata or {}
        subject = str(metadata.get("subject") or "note")
        # predicate default: identificador NOVO por chamada (nao reusa
        # 'note' fixo) -- senao toda chamada sem predicate explicito
        # substituiria (supersede) a nota anterior do mesmo subject, o
        # que so' faz sentido quando o chamador PEDE isso de proposito
        # passando um predicate estavel.
        from core.types import new_id
        predicate = str(metadata.get("predicate") or f"note_{new_id('n')[3:]}")
        reason = metadata.get("reason")

        fact = self.memory.remember(subject, predicate, content, reason=reason)
        logger.info("store_memory subject=%s predicate=%s content_len=%d superseded=%s", subject, predicate, len(content), bool(fact.supersedes))
        return {
            "id": fact.id,
            "subject": fact.subject,
            "predicate": fact.predicate,
            "superseded_id": fact.supersedes,
            "created_at": fact.created_at,
        }

    # ------------------------------------------------------------------
    # air_get_context -- query -> planner -> retrieval -> structural
    # memory -> context reconstruction -> resultado (fluxo pedido)
    # ------------------------------------------------------------------
    def get_context(self, query: str, max_tokens: int | None = None) -> dict:
        with self._lock:
            return self._get_context(query, max_tokens)

    def _get_context(self, query: str, max_tokens: int | None = None) -> dict:
        query = (query or "").strip()
        if not query:
            return {"error": "query vazia"}
        if len(query) > self.config.max_query_chars:
            return {"error": f"query excede o limite de {self.config.max_query_chars} caracteres"}

        max_tokens = max_tokens or self.config.max_context_tokens
        t0 = time.perf_counter()
        shared: dict = {}

        goal = self.planner.new_goal(f"reconstruir contexto para: {query}")
        t_retrieval = self.planner.add_task(goal, "retrieval: buscar fatos e eventos relevantes")
        t_structural = self.planner.add_task(goal, "structural_memory: resolver recencia/conflitos", depends_on=[t_retrieval.id])
        t_reconstruct = self.planner.add_task(goal, "context_reconstruction: montar contexto final dentro do orcamento de tokens", depends_on=[t_structural.id])

        from core.types import ActionResult, new_id

        def action_fn(task):
            if task.id == t_retrieval.id:
                raw = retrieval.search(self.world, self.memory, query, limit=max(20, self.config.default_search_limit * 4))
                shared["hits"] = raw["results"]
                shared["total_considered"] = raw["total_records_considered"]
                return ActionResult(id=new_id("act"), tool_name="mcp_retrieval", args={"query": query}, output=raw["results"])

            if task.id == t_structural.id:
                # recencia/conflito ja' resolvido na origem: memory.all_active()
                # (usado por retrieval.search) so' inclui o fato ACTIVE mais
                # recente por subject+predicate -- essa etapa so' garante que
                # nenhum id duplicado sobreviveu e ordena por score, deixando
                # explicito que o passo aconteceu (nao e' encenacao).
                seen = set()
                deduped = []
                for h in shared.get("hits", []):
                    key = (h["kind"], h["id"])
                    if key in seen:
                        continue
                    seen.add(key)
                    deduped.append(h)
                shared["hits"] = deduped
                return ActionResult(id=new_id("act"), tool_name="mcp_structural_memory", args={}, output=deduped)

            # t_reconstruct
            handles = []
            references = []
            budget_used = 0
            structural = self.config.enable_structural_memory
            for h in shared.get("hits", []):
                if h["kind"] == "fact":
                    md = h["metadata"]
                    fake_fact = Fact(id=h["id"], subject=md["subject"], predicate=md["predicate"], obj=md["obj"], reason=md.get("reason"), status=FactStatus.ACTIVE)
                    text = _render_fact(fake_fact, structural)
                else:
                    text = h["text"]

                item_tokens = tokens.count_tokens(text)["tokens"]
                if budget_used + item_tokens > max_tokens and handles:
                    break  # respeita o orcamento -- para de incluir, nao trunca texto no meio
                handle_id = self.context.put(text, kind=h["kind"], label=f"{h['kind']}:{h['id']}", pinned=True)
                handles.append(handle_id)
                references.append({"kind": h["kind"], "id": h["id"], "score": h["score"]})
                budget_used += item_tokens

            rendered = self.context.render(handles)
            output = {"context": rendered, "reference_count": len(handles)}
            shared["rendered"] = rendered
            shared["references"] = references
            return ActionResult(id=new_id("act"), tool_name="mcp_context_reconstruction", args={}, output=output)

        self.planner.run_all(goal, action_fn)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        rendered_context = shared.get("rendered", "")
        token_info = tokens.count_tokens(rendered_context)

        result = {
            "query": query,
            "context": rendered_context,
            "references": shared.get("references", []),
            "reference_count": len(shared.get("references", [])),
            "total_records_considered": shared.get("total_considered", 0),
            "tokens": token_info,
            "max_tokens_budget": max_tokens,
            "latency_ms": round(elapsed_ms, 3),
            "structural_memory_enabled": self.config.enable_structural_memory,
            "planner": [{"description": t.description, "status": t.status.value} for t in goal.tasks],
        }
        logger.info("get_context query_len=%d refs=%d tokens=%d method=%s latency_ms=%.1f", len(query), result["reference_count"], token_info["tokens"], token_info["method"], elapsed_ms)
        return result

    # ------------------------------------------------------------------
    # air_update_memory
    # ------------------------------------------------------------------
    def update_memory(self, id: str, content: str) -> dict:
        with self._lock:
            return self._update_memory(id, content)

    def _update_memory(self, id: str, content: str) -> dict:
        content = (content or "").strip()
        if not content:
            return {"error": "content vazio"}
        if len(content) > self.config.max_content_chars:
            return {"error": f"content excede o limite de {self.config.max_content_chars} caracteres"}

        fact = self.memory.get_fact(id)
        if fact is None:
            return {"error": f"memoria '{id}' nao encontrada"}
        if fact.status != FactStatus.ACTIVE:
            return {"error": f"memoria '{id}' nao esta' ACTIVE (status atual: {fact.status.value}) -- so' e' possivel atualizar memoria ativa"}

        # "atualizar" preserva historico -- cria nova versao que supersede
        # a antiga, mesmo mecanismo de recencia ja' validado, nao
        # sobrescreve a linha antiga em lugar (regra 10 do pedido).
        new_fact = self.memory.remember(fact.subject, fact.predicate, content, reason=f"atualizado via air_update_memory (versao anterior: {fact.id})")
        logger.info("update_memory old_id=%s new_id=%s", fact.id, new_fact.id)
        return {"id": new_fact.id, "previous_id": fact.id, "subject": new_fact.subject, "predicate": new_fact.predicate}

    # ------------------------------------------------------------------
    # air_delete_memory
    # ------------------------------------------------------------------
    def delete_memory(self, id: str) -> dict:
        with self._lock:
            return self._delete_memory(id)

    def _delete_memory(self, id: str) -> dict:
        fact = self.memory.get_fact(id)
        if fact is None:
            return {"error": f"memoria '{id}' nao encontrada"}
        if fact.status == FactStatus.DELETED:
            return {"error": f"memoria '{id}' ja' estava deletada"}

        self.memory.forget(id)
        logger.info("delete_memory id=%s", id)
        return {"id": id, "deleted": True}
