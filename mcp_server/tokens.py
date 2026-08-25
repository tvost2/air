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

MODEL_NAME = "HuggingFaceTB/SmolLM2-360M-Instruct"

_tokenizer = None
_tokenizer_load_failed = False


def _get_tokenizer():
    """Carrega SO' do cache local (local_files_only=True) -- um servidor
    MCP tem que responder rapido; medido nesta maquina, deixar o
    transformers checar a Hugging Face Hub por atualizacao (mesmo com o
    modelo ja' em cache) levou ~130s na primeira chamada, o que estouraria
    qualquer timeout razoavel de tool call. Sem acesso local ao cache,
    falha rapido e cai pra' heuristica em vez de tentar baixar."""
    global _tokenizer, _tokenizer_load_failed
    if _tokenizer is not None or _tokenizer_load_failed:
        return _tokenizer
    try:
        from transformers import AutoTokenizer
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, local_files_only=True)
    except Exception:
        _tokenizer_load_failed = True
        _tokenizer = None
    return _tokenizer


def count_tokens(text: str) -> dict:
    """Retorna {'tokens': int, 'method': str}. method e' sempre honesto
    sobre como o numero foi obtido."""
    if not text:
        return {"tokens": 0, "method": "empty"}
    tok = _get_tokenizer()
    if tok is not None:
        try:
            return {"tokens": len(tok(text)["input_ids"]), "method": f"tokenizer:{MODEL_NAME}"}
        except Exception:
            pass
    # heuristica de fallback, deixada explicita no campo 'method'
    return {"tokens": max(1, len(text) // 4), "method": "heuristic_chars_div_4"}
