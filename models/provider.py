"""
AIR models -- abstracao de provedor de modelo.

Pesquisa (docs/ECOSYSTEM_RESEARCH.md secao 1): LiteLLM ja resolve isso
(100+ provedores, incl. Ollama/vLLM local) -- aqui e' so' a interface fina
que o resto do AIR usa, com um adapter LiteLLM por baixo. Ressalva real
registrada na pesquisa: ataque de supply chain em pacotes PyPI do LiteLLM
em mar/2026 -- por isso o import e' tardio (lazy) e opcional, o AIR nao
quebra se litellm nao estiver instalado ou se o usuario preferir outro
adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class ModelResponse:
    text: str
    input_tokens: int
    output_tokens: int
    raw: object = None


class ModelProvider(Protocol):
    def complete(self, prompt: str, *, model: str, max_tokens: int = 512, **kwargs) -> ModelResponse: ...


class LiteLLMProvider:
    """Adapter fino sobre litellm.completion. Import tardio de proposito
    (ver docstring do modulo)."""

    def complete(self, prompt: str, *, model: str, max_tokens: int = 512, **kwargs) -> ModelResponse:
        import litellm  # import tardio -- so' exige a dependencia se este provider for realmente usado

        resp = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            **kwargs,
        )
        choice = resp.choices[0]
        usage = resp.usage
        return ModelResponse(
            text=choice.message.content,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            raw=resp,
        )


class HFLocalProvider:
    """Adapter pra' modelo local via transformers -- mesmo modelo usado
    nos experimentos struct-reasoning desta sessao (SmolLM2-360M-Instruct,
    CPU), reaproveitado de proposito pra' o demo do AIR nao depender de
    chave de API nem rede em tempo de execucao (so' no primeiro download,
    ja' em cache local desta maquina)."""

    def __init__(self, model_name: str = "HuggingFaceTB/SmolLM2-360M-Instruct"):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self._torch = torch
        # local_files_only=True -- mesma correcao ja aplicada em
        # mcp_server/tokens.py, faltava aqui: sem essa flag,
        # from_pretrained() checa o Hugging Face Hub por atualizacao
        # mesmo com o modelo ja em cache local, e isso foi medido nesta
        # sessao em ~130s so' pra essa checagem (ver README/tokens.py).
        # HFLocalProvider carrega EXATAMENTE o mesmo modelo
        # (SmolLM2-360M-Instruct) que tokens.py ja' tinha essa protecao --
        # faltava replicar aqui, mesma classe de lentidao, local diferente.
        self.tok = AutoTokenizer.from_pretrained(model_name, local_files_only=True)
        self.model = AutoModelForCausalLM.from_pretrained(model_name, dtype=torch.float32, local_files_only=True)
        self.model.eval()

    def complete(self, prompt: str, *, model: str = "local", max_tokens: int = 60, **kwargs) -> ModelResponse:
        inputs = self.tok(prompt, return_tensors="pt")
        n_in = inputs["input_ids"].shape[1]
        with self._torch.no_grad():
            out = self.model.generate(
                **inputs, max_new_tokens=max_tokens, do_sample=False, pad_token_id=self.tok.eos_token_id,
            )
        gen = out[0][n_in:]
        text = self.tok.decode(gen, skip_special_tokens=True)
        return ModelResponse(text=text.strip(), input_tokens=int(n_in), output_tokens=int(gen.shape[0]), raw=out)


class EchoProvider:
    """Provider trivial sem dependencia externa -- usado em testes e no
    exemplo, pra' nao exigir chave de API/rede so' pra validar o runtime."""

    def complete(self, prompt: str, *, model: str = "echo", max_tokens: int = 512, **kwargs) -> ModelResponse:
        text = prompt[-max_tokens:]
        return ModelResponse(text=text, input_tokens=len(prompt.split()), output_tokens=len(text.split()))
