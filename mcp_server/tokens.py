"""
AIR mcp_server -- contagem de token honesta (regra 9 do pedido do
usuario: "se nao houver tokenizer apropriado, deixar claro que a metrica
e' estimativa baseada em heuristica").

Reaproveita o mesmo tokenizador usado nos experimentos struct-reasoning e
no benchmark do proprio AIR (SmolLM2-360M-Instruct via transformers) --
ja' esta' em cache local desta maquina, nao precisa de rede em uso normal.
Se transformers/o modelo nao estiver disponivel por qualquer motivo, cai
pra' uma heuristica de caracteres, e o metodo usado e' sempre relatado no
retorno -- nunca se afirma "tokens" sem dizer como foram contados.
"""
from __future__ import annotations

import threading

MODEL_NAME = "HuggingFaceTB/SmolLM2-360M-Instruct"

_tokenizer = None
_tokenizer_load_failed = False
_tokenizer_lock = threading.Lock()


def _get_tokenizer(blocking: bool = True):
    """Carrega SO' do cache local (local_files_only=True) -- um servidor
    MCP tem que responder rapido; medido nesta maquina, deixar o
    transformers checar a Hugging Face Hub por atualizacao (mesmo com o
    modelo ja' em cache) levou ~130s na primeira chamada, o que estouraria
    qualquer timeout razoavel de tool call. Mesmo so' do cache local, a
    primeira carga (import pesado do transformers + leitura em disco)
    ainda mediu ~208s nesta maquina -- ver warm_tokenizer_async() abaixo,
    que existe exatamente por causa disso. Sem acesso local ao cache,
    falha rapido e cai pra' heuristica em vez de tentar baixar.

    blocking=True (default, usado por warm_tokenizer_async() -- quem
    dispara o carregamento de verdade tem que esperar ate' terminar):
    trava normal, se outra thread ja' estiver carregando so' espera a
    MESMA carga terminar, nunca dispara uma segunda.

    blocking=False (usado por count_tokens() -- ver comentario la'):
    tenta pegar a trava SEM esperar; se estiver ocupada (warmup rodando,
    ou outra chamada real concorrente ja' carregando), devolve None na
    hora em vez de bloquear ate' 208s -- quem chamou cai pra' heuristica,
    honestamente reportada no campo 'method' (regra 9: nunca afirma
    precisao que nao tem, e' preferivel a resposta RAPIDA e honesta do
    que a resposta EXATA depois de quase 4 minutos de espera silenciosa).
    Bug real corrigido, nao hipotetico: antes desta mudanca, uma tool
    call real que chegasse durante a janela de warmup ficava presa
    esperando o MESMO lock ate' o carregamento (que ela nao pediu e nao
    sabia que estava rolando) terminar -- limitacao ja' documentada no
    README ("nao ha' timeout/fallback pra esse caso especifico ainda")
    que ficou sem solucao ate' agora."""
    global _tokenizer, _tokenizer_load_failed
    if _tokenizer is not None or _tokenizer_load_failed:
        return _tokenizer
    if blocking:
        acquired = _tokenizer_lock.acquire()
    else:
        acquired = _tokenizer_lock.acquire(blocking=False)
        if not acquired:
            return None   # lock ocupado -- cai pra' heuristica em vez de esperar
    try:
        if _tokenizer is not None or _tokenizer_load_failed:
            return _tokenizer
        try:
            from transformers import AutoTokenizer
            _tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, local_files_only=True)
        except Exception:
            _tokenizer_load_failed = True
            _tokenizer = None
    finally:
        _tokenizer_lock.release()
    return _tokenizer


def warm_tokenizer_async() -> threading.Thread:
    """Dispara o carregamento do tokenizer numa thread separada, sem
    bloquear quem chamou. Existe pra' resolver o meio-termo entre as duas
    opcoes ja' tentadas e documentadas em mcp_server/server.py:main():

    - warmup SINCRONO antes do handshake MCP: bloqueava a resposta ao
      cliente por ~208s, estourando o timeout de CONEXAO do Claude Code
      (30s) -- "connection timed out after 30000ms", real, observado.
    - SEM warmup nenhum (estado anterior): handshake responde na hora,
      mas a PRIMEIRA chamada real de air_get_context/air_search_context
      e' quem paga os ~208s, de forma sincrona e sem aviso -- ainda pode
      estourar o timeout de TOOL CALL de quem estiver chamando.

    Chamando isto logo no startup do servidor (antes de server.run()), o
    carregamento comeca em paralelo com o processo ja' respondendo ao
    handshake -- na pratica, o tempo entre "servidor conectado" e "agente
    faz a primeira chamada real" (descoberta de tools, planejamento) ja'
    cobre boa parte ou todo o carregamento, entao a primeira chamada real
    tende a encontrar o tokenizer pronto (ou espera so' o restante, nunca
    os 208s inteiros do zero).

    Seguro chamar mais de uma vez (chamadas depois da primeira retornam
    quase na hora, ja' que _tokenizer/_tokenizer_load_failed ja' estao'
    definidos) e seguro rodar concorrente com uma count_tokens() real --
    _tokenizer_lock garante que so' uma delas carrega de verdade."""
    thread = threading.Thread(target=_get_tokenizer, daemon=True, name="air-tokenizer-warmup")
    thread.start()
    return thread


def count_tokens(text: str) -> dict:
    """Retorna {'tokens': int, 'method': str}. method e' sempre honesto
    sobre como o numero foi obtido -- inclusive quando a resposta e'
    heuristica so' porque o tokenizer real esta' carregando em segundo
    plano nesse exato momento (ver _get_tokenizer(blocking=False))."""
    if not text:
        return {"tokens": 0, "method": "empty"}
    tok = _get_tokenizer(blocking=False)
    if tok is not None:
        try:
            return {"tokens": len(tok(text)["input_ids"]), "method": f"tokenizer:{MODEL_NAME}"}
        except Exception:
            pass
    # heuristica de fallback, deixada explicita no campo 'method'
    return {"tokens": max(1, len(text) // 4), "method": "heuristic_chars_div_4"}
