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

import difflib
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
    def search_context(self, query: str, limit: int | None = None, project: str | None = None) -> dict:
        with self._lock:
            return self._search_context(query, limit, project)

    def _search_context(self, query: str, limit: int | None = None, project: str | None = None) -> dict:
        query = (query or "").strip()
        if not query:
            return {"error": "query vazia"}
        if len(query) > self.config.max_query_chars:
            return {"error": f"query excede o limite de {self.config.max_query_chars} caracteres"}

        limit = limit or self.config.default_search_limit
        project = project or None  # "" (nao informado) vira None = sem escopo, nao "projeto vazio"
        result = retrieval.search(self.world, self.memory, query, limit=limit, project=project)
        logger.info("search_context query_len=%d project=%s results=%d latency_ms=%.1f", len(query), project, len(result["results"]), result["latency_ms"])
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
        # project: "" (default) = fato global, visivel em busca de
        # qualquer projeto. Passar um nome de projeto escopa o fato pra'
        # so' aparecer em busca feita com o mesmo project= (mais buscas
        # sem escopo nenhum, que continuam vendo tudo) -- e' o mecanismo
        # de isolamento entre "mundos" (ver retrieval.py / README).
        project = str(metadata.get("project") or "")

        fact = self.memory.remember(subject, predicate, content, reason=reason, project=project)
        logger.info("store_memory subject=%s predicate=%s project=%s content_len=%d superseded=%s", subject, predicate, project, len(content), bool(fact.supersedes))
        return {
            "id": fact.id,
            "subject": fact.subject,
            "predicate": fact.predicate,
            "project": fact.project,
            "superseded_id": fact.supersedes,
            "created_at": fact.created_at,
        }

    # ------------------------------------------------------------------
    # air_register_entity -- liga um artefato ja' construido (API,
    # frontend, modulo) ao World State, pra' uma tarefa futura achar via
    # air_search_context/air_get_context em vez de ser refeita do zero.
    # Grava em World State (nao Memory): isso e' "coisa que existe", nao
    # "fato/preferencia" -- mesma distincao de dominio que o resto do AIR
    # ja' aplica entre world/ e memory/.
    # ------------------------------------------------------------------
    def register_entity(self, kind: str, name: str, attrs: dict | None = None, project: str = "") -> dict:
        with self._lock:
            return self._register_entity(kind, name, attrs, project)

    def _register_entity(self, kind: str, name: str, attrs: dict | None = None, project: str = "") -> dict:
        kind = (kind or "").strip()
        name = (name or "").strip()
        if not kind:
            return {"error": "kind vazio"}
        if not name:
            return {"error": "name vazio"}

        attrs = dict(attrs or {})
        import json as _json
        serialized = _json.dumps(attrs, ensure_ascii=False)
        if len(serialized) > self.config.max_content_chars:
            return {"error": f"attrs excede o limite de {self.config.max_content_chars} caracteres serializados"}

        # custo real (tokenizador, nao heuristica quando disponivel -- ver
        # mcp_server/tokens.py) de trazer esta entidade pro contexto se
        # alguem reusar em vez de refazer -- e' o numero honesto que
        # justifica "readaptar em vez de reescrever": nao afirma quantos
        # tokens FORAM economizados construindo (isso nao e' medivel daqui
        # pra' tras), so' quanto custa TRAZER de volta, que e' medivel de
        # verdade.
        entity_text = f"entidade {kind} {name} {serialized}"
        cost = tokens.count_tokens(entity_text)
        attrs["_context_cost_tokens"] = cost["tokens"]
        attrs["_context_cost_method"] = cost["method"]

        # idempotente por nome: se ja' existe uma entidade com esse nome,
        # nao duplica -- devolve a existente. Pra' de fato ATUALIZAR uma
        # entidade existente, use air_update_entity (nao existia ate' esta
        # versao -- ver metodo update_entity abaixo).
        existing = self.world.find_entity_by_name(name)
        if existing is not None:
            logger.info("register_entity name=%s ja existe (id=%s), nao duplicado", name, existing.id)
            return {
                "id": existing.id, "kind": existing.kind, "name": existing.name,
                "project": existing.project, "already_existed": True,
                "context_cost_tokens": existing.attrs.get("_context_cost_tokens"),
                "context_cost_method": existing.attrs.get("_context_cost_method"),
            }

        # aviso (nao bloqueia) de quase-duplicata: dedup por nome EXATO
        # (acima) nao pega "air" vs "air-runtime" pro mesmo artefato,
        # achado real registrando em sessoes MCP diferentes (ver README).
        # difflib.SequenceMatcher e' stdlib, sem dependencia nova; 0.82 e'
        # limiar deliberadamente conservador (poucos falsos positivos) --
        # isto SO' avisa no retorno, nunca impede o registro nem faz merge
        # automatico (merge errado destroi dado, e' pior que duplicata).
        possible_duplicates = self._find_near_duplicates(name, project)

        entity = self.world.entity(name, kind=kind, attrs=attrs, project=project)
        logger.info("register_entity id=%s kind=%s name=%s project=%s tokens=%s near_dupes=%d", entity.id, kind, name, project, cost["tokens"], len(possible_duplicates))
        return {
            "id": entity.id, "kind": entity.kind, "name": entity.name,
            "project": entity.project, "already_existed": False,
            "context_cost_tokens": cost["tokens"], "context_cost_method": cost["method"],
            "possible_duplicates": possible_duplicates,
        }

    def _find_near_duplicates(self, name: str, project: str, threshold: float = 0.82) -> list[dict]:
        """Dois criterios, nao um so' -- medido com o proprio caso real que
        motivou isto ("air" vs "air-runtime"): SequenceMatcher.ratio() e'
        normalizado pelo tamanho TOTAL das duas strings, entao um prefixo
        exato de um nome bem mais curto dentro de um mais longo ("air"
        dentro de "air-runtime", 3 vs 11 caracteres) da' ratio baixo
        (~0.43) mesmo sendo o exemplo canonico de quase-duplicata -- so'
        ratio>=threshold nao pegava o proprio caso documentado no README.
        Por isso: OR com "um nome e' substring do outro" (comparacao
        case-insensitive), com piso de 3 caracteres pro nome mais curto
        pra' nao disparar em nomes genericos/curtos demais (ex: 'api' seria
        substring de quase qualquer coisa)."""
        name_low = name.lower()
        candidates = self.world.all_entities(project=project or None)
        hits = []
        for e in candidates:
            other_low = e.name.lower()
            if other_low == name_low:
                continue
            ratio = difflib.SequenceMatcher(None, name_low, other_low).ratio()
            shorter, longer = sorted((name_low, other_low), key=len)
            is_prefix_or_substring = len(shorter) >= 3 and shorter in longer
            if ratio >= threshold or is_prefix_or_substring:
                hits.append({"id": e.id, "name": e.name, "similarity": round(max(ratio, 0.0), 3)})
        hits.sort(key=lambda h: h["similarity"], reverse=True)
        return hits

    # ------------------------------------------------------------------
    # air_update_entity -- atualiza kind/attrs de uma entidade existente
    # sem precisar delete+register (limitacao conhecida ate' aqui, ver
    # README). Nao existe update de name/project de proposito: mudar o
    # identificador ou o escopo de isolamento de uma entidade existente
    # e' realisticamente "e' outra entidade", nao "a mesma atualizada" --
    # quem precisar disso usa delete_entity + register_entity de novo.
    # ------------------------------------------------------------------
    def update_entity(self, id: str, attrs: dict | None = None, kind: str | None = None, merge_attrs: bool = True) -> dict:
        with self._lock:
            return self._update_entity(id, attrs, kind, merge_attrs)

    def _update_entity(self, id: str, attrs: dict | None = None, kind: str | None = None, merge_attrs: bool = True) -> dict:
        if attrs is None and not kind:
            return {"error": "informe attrs e/ou kind -- nada pra' atualizar"}
        if attrs is not None:
            import json as _json
            serialized = _json.dumps(attrs, ensure_ascii=False)
            if len(serialized) > self.config.max_content_chars:
                return {"error": f"attrs excede o limite de {self.config.max_content_chars} caracteres serializados"}

        updated = self.world.update_entity(id, attrs=attrs, kind=kind or None, merge_attrs=merge_attrs)
        if updated is None:
            return {"error": f"entidade '{id}' nao encontrada"}
        logger.info("update_entity id=%s kind=%s merge_attrs=%s", id, updated.kind, merge_attrs)
        return {"id": updated.id, "kind": updated.kind, "name": updated.name, "project": updated.project, "attrs": updated.attrs}

    # ------------------------------------------------------------------
    # air_delete_entity -- remove entidade por id (hard delete, ver
    # world/state.py; Entity nao tem status pra' soft-delete como Fact).
    # Existe principalmente pra' corrigir duplicata/engano de registro --
    # air_register_entity so' deduplica por NAME exato, nomes quase-iguais
    # (achado real: "air" vs "air-runtime" pro mesmo projeto, registrados
    # em sessoes MCP diferentes) passam direto e viram entidade duplicada.
    # ------------------------------------------------------------------
    def delete_entity(self, id: str) -> dict:
        with self._lock:
            return self._delete_entity(id)

    def _delete_entity(self, id: str) -> dict:
        existing = self.world.get_entity(id)
        if existing is None:
            return {"error": f"entidade '{id}' nao encontrada"}
        self.world.delete_entity(id)
        logger.info("delete_entity id=%s name=%s", id, existing.name)
        return {"id": id, "name": existing.name, "deleted": True}

    # ------------------------------------------------------------------
    # air_register_relation -- liga duas entidades ja' registradas ("X
    # depende de Y", "X hospeda Y"). World State prometia entidade/RELACAO/
    # evento consultavel desde o inicio (ver docs/ECOSYSTEM_RESEARCH.md
    # 2.2 e world/state.py:dependents_of, que ja' fazia a CONSULTA) mas so'
    # entidade tinha tool MCP de escrita ate' esta versao -- limitacao
    # conhecida documentada no README, fechada aqui.
    # ------------------------------------------------------------------
    def register_relation(self, source_id: str, kind: str, target_id: str, project: str = "") -> dict:
        with self._lock:
            return self._register_relation(source_id, kind, target_id, project)

    def _register_relation(self, source_id: str, kind: str, target_id: str, project: str = "") -> dict:
        source_id = (source_id or "").strip()
        kind = (kind or "").strip()
        target_id = (target_id or "").strip()
        if not source_id:
            return {"error": "source_id vazio"}
        if not kind:
            return {"error": "kind vazio"}
        if not target_id:
            return {"error": "target_id vazio"}

        # valida que as duas pontas existem -- diferente de delete_entity
        # (que tolera relation orfa' apontando pra' id ja' apagado, ver
        # world/state.py), aqui e' registro NOVO: nao ha' motivo legitimo
        # pra' criar uma relacao que ja' nasce apontando pro vazio, e' quase
        # sempre id errado por engano de quem chamou.
        source = self.world.get_entity(source_id)
        if source is None:
            return {"error": f"source_id '{source_id}' nao encontrado -- registre a entidade antes com air_register_entity"}
        target = self.world.get_entity(target_id)
        if target is None:
            return {"error": f"target_id '{target_id}' nao encontrado -- registre a entidade antes com air_register_entity"}

        relation = self.world.relation(source_id, kind, target_id, project=project)
        logger.info("register_relation id=%s %s --%s--> %s project=%s", relation.id, source_id, kind, target_id, project)
        return {
            "id": relation.id, "source_id": source_id, "source_name": source.name,
            "kind": kind, "target_id": target_id, "target_name": target.name,
            "project": relation.project,
        }

    # ------------------------------------------------------------------
    # air_get_context -- query -> planner -> retrieval -> structural
    # memory -> context reconstruction -> resultado (fluxo pedido)
    # ------------------------------------------------------------------
    def get_context(self, query: str, max_tokens: int | None = None, project: str | None = None) -> dict:
        with self._lock:
            return self._get_context(query, max_tokens, project)

    def _get_context(self, query: str, max_tokens: int | None = None, project: str | None = None) -> dict:
        query = (query or "").strip()
        if not query:
            return {"error": "query vazia"}
        if len(query) > self.config.max_query_chars:
            return {"error": f"query excede o limite de {self.config.max_query_chars} caracteres"}

        max_tokens = max_tokens or self.config.max_context_tokens
        project = project or None  # "" (nao informado) vira None = sem escopo
        t0 = time.perf_counter()
        shared: dict = {}

        goal = self.planner.new_goal(f"reconstruir contexto para: {query}")
        t_retrieval = self.planner.add_task(goal, "retrieval: buscar fatos e eventos relevantes")
        t_structural = self.planner.add_task(goal, "structural_memory: resolver recencia/conflitos", depends_on=[t_retrieval.id])
        t_reconstruct = self.planner.add_task(goal, "context_reconstruction: montar contexto final dentro do orcamento de tokens", depends_on=[t_structural.id])

        from core.types import ActionResult, new_id

        def action_fn(task):
            if task.id == t_retrieval.id:
                raw = retrieval.search(self.world, self.memory, query, limit=max(20, self.config.default_search_limit * 4), project=project)
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
            "project": project,
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
        #
        # project=fact.project e' CRITICO aqui, nao cosmetico -- bug real
        # encontrado testando (nao hipotetico): sem isso, remember() usa
        # o default project="" pra' DUAS coisas ao mesmo tempo: (1) a
        # propria busca interna de "qual fato ativo supersede" (subject+
        # predicate+project), que com project errado nunca acha o `fact`
        # que acabamos de buscar por id alguns linhas acima -- o fato
        # original ficava ACTIVE pra sempre, nunca SUPERSEDED; (2) o
        # project do fato NOVO, que virava "" (global) mesmo quando o
        # original era escopado -- o conteudo atualizado de um projeto
        # escopado vazava GLOBALMENTE, aparecendo em busca de QUALQUER
        # outro projeto. Exatamente o tipo de contaminacao cross-projeto
        # que o mecanismo de `project` inteiro existe pra' evitar (ver
        # README "Isolamento entre projetos/sessoes") -- so' que aqui
        # dentro da propria tool que deveria preservar o escopo.
        new_fact = self.memory.remember(fact.subject, fact.predicate, content, reason=f"atualizado via air_update_memory (versao anterior: {fact.id})", project=fact.project)
        logger.info("update_memory old_id=%s new_id=%s project=%s", fact.id, new_fact.id, new_fact.project)
        # project no retorno -- mesma paridade de store_memory (que ja'
        # inclui), pra' quem chama confirmar visualmente o escopo sem
        # precisar adivinhar ou confiar cegamente que foi preservado.
        return {"id": new_fact.id, "previous_id": fact.id, "subject": new_fact.subject, "predicate": new_fact.predicate, "project": new_fact.project}

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
