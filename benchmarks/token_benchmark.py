"""
AIR benchmarks -- compara tokens gastos por um agente "tradicional"
(historico completo reenviado a cada turno, o jeito que praticamente todo
framework de agente hoje monta o prompt) contra um agente rodando sobre o
AIR runtime (World State pra fato estrutural consultavel, Context Engine
pra referencia por ID em vez de reenvio de output de tool).

Metodologia igual ao resto desta sessao (struct-reasoning): tokenizador
real (SmolLM2, ja' usado nos outros experimentos, CPU, sem API paga),
medicao real, nao estimativa. Cenario sintetico mas honesto sobre ser
sintetico -- nao e' log de producao real, e' um cenario de tamanho e
formato plausiveis (debug de outage de servico), construido pra' ser
reproduzivel.

Cenario: agente investigando por que um servico caiu. A cada turno: (1)
usuario pergunta algo, (2) agente chama uma tool (ex: ler log, consultar
estado), (3) tool devolve um resultado de tamanho realista (log excerpt).
Isso se repete por N turnos, crescendo o historico.

Condicao A (tradicional): prompt do turno T = instrucao de sistema +
TODOS os turnos anteriores (pergunta + output de tool inteiro) + pergunta
atual. Cresce linear/quadraticamente com o numero de turnos, como agente
de historico completo faz de verdade.

Condicao B (AIR): prompt do turno T = instrucao de sistema (mesma) +
fatos relevantes do World State/Memory (pequenos, consultados por
consulta direta, nao por historico) + Context Engine render() dos outputs
de tool (pequeno inteiro, grande vira so' referencia+resumo) + pergunta
atual. NAO reenvia tool outputs antigos que ja' viraram handle.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import statistics as st

from transformers import AutoTokenizer

from context.engine import ContextEngine
from world.state import WorldState
from memory.store import MemoryStore

MODEL_NAME = "HuggingFaceTB/SmolLM2-360M-Instruct"

SYSTEM_INSTR = (
    "Voce e' um agente investigando a causa de uma queda de servico. "
    "Use as informacoes disponiveis pra' responder a pergunta do usuario."
)

# um "log excerpt" de tamanho realista pra' cada turno -- fixo pra'
# reprodutibilidade, mas variando levemente por turno pra' nao ser
# artificialmente identico (o que infla economia de forma irreal).
def make_log_excerpt(turn: int) -> str:
    lines = [f"[2026-08-24 10:{10+turn:02d}:{i:02d}] servico=api pid={1000+turn} nivel=INFO mensagem=processando requisicao {i} status=200 latencia_ms={40+i}" for i in range(25)]
    lines.append(f"[2026-08-24 10:{10+turn:02d}:59] servico=api pid={1000+turn} nivel=ERROR mensagem=falha ao conectar no banco de dados apos {3+turn} tentativas")
    return "\n".join(lines)


def build_traditional_prompts(n_turns: int) -> list[str]:
    """Condicao A: cada prompt inclui TODO o historico anterior (pergunta
    + tool output completo de cada turno passado), igual agente de
    historico completo reenvia hoje."""
    prompts = []
    history = []
    for t in range(n_turns):
        question = f"Por que a chamada {t} falhou?"
        log = make_log_excerpt(t)
        history_text = "\n\n".join(history)
        prompt = f"{SYSTEM_INSTR}\n\n{history_text}\n\nUsuario: {question}\nAgente (chama tool ler_log):\n{log}\nResposta:"
        prompts.append(prompt)
        history.append(f"Usuario: {question}\nAgente (chama tool ler_log):\n{log}")
    return prompts


def build_air_prompts(n_turns: int) -> list[str]:
    """Condicao B: World State guarda o fato estrutural (servico->causa),
    Context Engine guarda os logs por handle -- só o log do turno ATUAL
    entra inteiro no prompt (senão nenhuma tool nunca seria lida de
    verdade), os anteriores ficam só como referência, e o AGENTE não
    precisa que o usuário repita nada porque a causa raiz já está no
    World State, consultável, não em prosa reenviada."""
    world = WorldState()
    ctx = ContextEngine()
    world.entity("api", kind="service", id="api")

    prompts = []
    for t in range(n_turns):
        question = f"Por que a chamada {t} falhou?"
        log = make_log_excerpt(t)
        handle = ctx.put(log, kind="tool_output", label=f"log do turno {t}", pinned=(True if t == n_turns - 1 else False))

        world.event("api", "connection_failed", {"turn": t})
        recent_events = world.events_of("api", limit=3)
        state_summary = "; ".join(f"evento={e.kind} turno={e.payload.get('turn')}" for e in recent_events)

        # so' os handles ainda relevantes (ultimos 2 turnos) entram no render --
        # os mais antigos existem no world state como evento, nao precisam
        # do log bruto reenviado pra' responder "por que falhou".
        relevant_handles = [h for h in list(ctx._items.keys())[-2:]]
        rendered_ctx = ctx.render(relevant_handles)

        prompt = f"{SYSTEM_INSTR}\n\nEstado conhecido (World State): {state_summary}\n\n{rendered_ctx}\n\nUsuario: {question}\nResposta:"
        prompts.append(prompt)
    return prompts


def run_scenario(tok, n_turns: int) -> dict:
    trad_prompts = build_traditional_prompts(n_turns)
    air_prompts = build_air_prompts(n_turns)

    trad_tokens = [len(tok(p)["input_ids"]) for p in trad_prompts]
    air_tokens = [len(tok(p)["input_ids"]) for p in air_prompts]

    total_trad = sum(trad_tokens)
    total_air = sum(air_tokens)

    return {
        "n_turns": n_turns,
        "total_tokens_traditional": total_trad,
        "total_tokens_air": total_air,
        "savings_pct": 1 - total_air / total_trad if total_trad else 0,
        "last_turn_tokens_traditional": trad_tokens[-1],
        "last_turn_tokens_air": air_tokens[-1],
        "last_turn_savings_pct": 1 - air_tokens[-1] / trad_tokens[-1] if trad_tokens[-1] else 0,
    }


def main():
    print(f"Carregando tokenizador {MODEL_NAME}...")
    tok = AutoTokenizer.from_pretrained(MODEL_NAME)

    scenarios = {"curto": 5, "medio": 15, "longo": 30}
    results = {}
    for label, n in scenarios.items():
        print(f"Rodando cenario '{label}' ({n} turnos)...")
        results[label] = run_scenario(tok, n)

    Path("results").mkdir(exist_ok=True)
    with open("results/token_benchmark.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("\n=== Resultado: tokens totais gastos na sessao inteira ===")
    for label, r in results.items():
        print(f"\n[{label}] {r['n_turns']} turnos")
        print(f"  Tradicional (historico completo): {r['total_tokens_traditional']:,} tokens")
        print(f"  AIR (World State + Context Engine): {r['total_tokens_air']:,} tokens")
        print(f"  Economia agregada: {r['savings_pct']:.1%}")
        print(f"  Ultimo turno isolado -- tradicional: {r['last_turn_tokens_traditional']:,}, AIR: {r['last_turn_tokens_air']:,} (economia: {r['last_turn_savings_pct']:.1%})")

    print("\nSalvo em results/token_benchmark.json")


if __name__ == "__main__":
    main()
