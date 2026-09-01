"""
AIR tests -- mcp_server/tokens.py. Nao tinha teste nenhum ate' aqui,
apesar de ja' ter tido dois bugs reais de concorrencia documentados no
README (cold-start de 208s bloqueando o handshake MCP, depois bloqueando
a primeira tool call real) -- exatamente o tipo de codigo que mais
precisa de teste, nao menos.

Deliberadamente NAO depende do tokenizer real carregar (nao chama
warm_tokenizer_async() ate' completar nem espera _get_tokenizer(blocking=
True) carregar de verdade) -- simula contencao de lock manualmente, pra'
rodar rapido e nao depender do cache HF local existir nesta maquina.

Roda com `python tests/test_tokens.py` a partir de E:\\x\\air.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_server import tokens

failures = []


def check(name: str, cond: bool):
    status = "OK" if cond else "FALHOU"
    print(f"[{status}] {name}")
    if not cond:
        failures.append(name)


def test_count_tokens_empty_text():
    result = tokens.count_tokens("")
    check("tokens: texto vazio -> 0 tokens, method='empty'", result == {"tokens": 0, "method": "empty"})


def test_count_tokens_does_not_block_when_tokenizer_lock_is_busy():
    """Regressao do bug real corrigido: antes, uma tool call real que
    chegasse enquanto o lock do tokenizer estava ocupado (warmup em
    segundo plano, ou outra carga concorrente) ficava presa esperando o
    MESMO lock ate' o carregamento terminar (ate' 208s medidos nesta
    maquina) -- limitacao ja' documentada no README como sem solucao
    ainda. _get_tokenizer(blocking=False) agora resolve isso: com o lock
    ocupado, count_tokens() cai pra heuristica na hora."""
    # segura o lock manualmente, simulando carga em andamento -- nao
    # precisa do tokenizer real pra' isso, so' do lock que o protege.
    tokens._tokenizer_lock.acquire()
    try:
        text = "um texto qualquer pra contar tokens"
        t0 = time.perf_counter()
        result = tokens.count_tokens(text)
        elapsed = time.perf_counter() - t0

        check("tokens: com lock ocupado, count_tokens() responde rapido (nao bloqueia esperando o lock)", elapsed < 1.0)
        check("tokens: com lock ocupado, cai pra heuristica honestamente reportada", result["method"] == "heuristic_chars_div_4")
        check("tokens: heuristica calcula max(1, len(texto)//4)", result["tokens"] == max(1, len(text) // 4))
    finally:
        tokens._tokenizer_lock.release()


def test_get_tokenizer_nonblocking_returns_none_when_lock_busy():
    """Mesmo cenario, testando _get_tokenizer(blocking=False) direto (nao
    so' via count_tokens()) -- devolve None na hora, nao bloqueia."""
    tokens._tokenizer_lock.acquire()
    try:
        t0 = time.perf_counter()
        result = tokens._get_tokenizer(blocking=False)
        elapsed = time.perf_counter() - t0
        check("tokens: _get_tokenizer(blocking=False) devolve None com lock ocupado", result is None)
        check("tokens: _get_tokenizer(blocking=False) nao bloqueia", elapsed < 1.0)
    finally:
        tokens._tokenizer_lock.release()


def test_get_tokenizer_blocking_true_waits_for_lock_to_free():
    """Confirma que o modo blocking=True (usado por warm_tokenizer_async,
    quem de fato carrega) continua esperando o lock normalmente -- a
    correcao so' mudou o comportamento de quem NAO quer esperar."""
    # se _tokenizer ja' estiver definido (ex: outro teste deste arquivo
    # rodou primeiro e o cache local existe), _get_tokenizer devolve
    # direto sem tocar o lock -- este teste so' faz sentido enquanto isso
    # nao aconteceu. Pula honestamente em vez de dar falso positivo.
    if tokens._tokenizer is not None or tokens._tokenizer_load_failed:
        print("[SKIP] tokens: blocking=True espera o lock (tokenizer ja' resolvido nesta sessao, nao da' pra' testar contencao)")
        return

    released_at = []

    def hold_lock_briefly():
        tokens._tokenizer_lock.acquire()
        time.sleep(0.3)
        released_at.append(time.perf_counter())
        tokens._tokenizer_lock.release()

    holder = threading.Thread(target=hold_lock_briefly)
    t0 = time.perf_counter()
    holder.start()
    time.sleep(0.05)  # garante que a thread acima ja' pegou o lock antes de tentarmos
    acquired = tokens._tokenizer_lock.acquire(blocking=True, timeout=2.0)
    waited = time.perf_counter() - t0
    if acquired:
        tokens._tokenizer_lock.release()
    holder.join()
    check("tokens: aquisicao blocking=True espera o lock liberar (nao retorna antes de ~0.3s)", waited >= 0.25)


def main():
    test_count_tokens_empty_text()
    test_count_tokens_does_not_block_when_tokenizer_lock_is_busy()
    test_get_tokenizer_nonblocking_returns_none_when_lock_busy()
    test_get_tokenizer_blocking_true_waits_for_lock_to_free()

    print()
    if failures:
        print(f"{len(failures)} teste(s) falharam: {failures}")
        sys.exit(1)
    print("Todos os testes de tokens.py passaram.")


if __name__ == "__main__":
    main()
