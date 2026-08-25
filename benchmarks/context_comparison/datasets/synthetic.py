"""
benchmarks/context_comparison/datasets -- dataset sintetico, deterministico
(seed fixa) e reprodutivel, pra' comparar abordagens de reducao/gestao de
contexto.

Dominio: base de conhecimento operacional fictícia (servicos, projetos,
pessoas) -- mesmo estilo do resto do AIR (server/api/dependencia), mas um
dataset NOVO e independente, criado so' pra' este benchmark. Nomes e fatos
sao inventados, nao ha' informacao pessoal real.

6 categorias (pedido minimo: 5), cada uma testando uma capacidade
diferente que reducao de contexto pode quebrar:

- factual_simple: 1 fato, lookup direto.
- multi_hop: resposta exige combinar 2 fatos distintos (projeto->pessoa,
  pessoa->preferencia).
- recency_conflict: 2 declaracoes conflitantes sobre a MESMA coisa; a
  mais recente e' a correta -- testa se a abordagem usa recencia ou so'
  pega o primeiro match.
- long_distance: contexto longo (~40+ frases de preenchimento) com o
  fato relevante enterrado numa posicao controlada ("lost in the
  middle").
- irrelevant_context: varios fatos do MESMO formato sobre OUTRAS
  entidades (distratores dificeis, nao ruido generico) + o fato certo.
- repeated_information: o mesmo fato repetido varias vezes ao longo do
  contexto (redundancia) -- testa desperdicio de token em dedup.

Cada caso tem exatamente os campos pedidos: context, question,
expected_answer, relevant_information, difficulty, category.

Resposta esperada e' sempre uma string curta (nome, data curta, termo
tecnico) pra' permitir scoring por exact/substring match automatizado --
nao ha' juiz semantico disponivel nesta maquina, entao perguntas foram
desenhadas de proposito pra' ter resposta canonica curta (mesma decisao
tomada nos experimentos struct-reasoning desta sessao).
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field, asdict
from pathlib import Path

SEED = 20260824  # data desta sessao, fixa -- reproducibilidade exata

PEOPLE = ["Ana Ferreira", "Bruno Salles", "Carla Nunes", "Diego Prado", "Elena Cardoso",
          "Fabio Lacerda", "Gabriela Tavares", "Hugo Marchetti", "Iris Bonfim", "Joao Peixoto",
          "Karina Estevez", "Luis Amorim"]
SERVICES = ["auth-service", "billing-service", "notifications-service", "search-service",
            "payments-service", "catalog-service", "shipping-service", "reporting-service"]
PROJECTS = ["orion", "nebula", "atlas", "phoenix", "zephyr", "vertex", "quasar", "helix"]
CHANNELS = ["Slack", "email", "telefone", "Teams"]
DATABASES = ["PostgreSQL", "MySQL", "MongoDB", "DynamoDB", "Cassandra"]
DATES_OLD = ["janeiro/2024", "marco/2024", "maio/2024", "julho/2024"]
DATES_NEW = ["fevereiro/2026", "abril/2026", "junho/2026", "agosto/2026"]

FILLER_TEMPLATES = [
    "O time revisou o backlog do sprint na reuniao de {day}-feira.",
    "A documentacao do modulo de logging foi atualizada recentemente.",
    "Houve uma pausa para cafe as 15h durante a sessao de planejamento.",
    "O pipeline de CI levou em media 8 minutos para concluir os testes.",
    "A equipe de design entregou os novos mockups da tela de login.",
    "Um novo membro se juntou ao time de infraestrutura este mes.",
    "O relatorio mensal de metricas foi enviado para a lideranca.",
    "A sala de reunioes B foi reservada para o workshop de arquitetura.",
    "O certificado SSL do ambiente de staging foi renovado sem incidentes.",
    "Foi feita uma limpeza de branches antigas no repositorio principal.",
    "O time de QA reportou zero bugs criticos na ultima rodada de testes.",
    "A politica de backup foi revisada e aprovada pelo comite tecnico.",
]

DAYS = ["segunda", "terca", "quarta", "quinta", "sexta"]


@dataclass
class Case:
    id: str
    category: str
    difficulty: str
    context: str
    question: str
    expected_answer: str
    relevant_information: list[str] = field(default_factory=list)
    # campo OPCIONAL, extra em relacao ao schema minimo pedido (context,
    # question, expected_answer, relevant_information, difficulty,
    # category) -- necessario so' pro adapter do AIR, que ingere fato
    # discreto (subject/predicate/obj), nao paragrafo cru. Cada entrada
    # mapeia uma sentenca "de fato" (nao filler) pra' a chave
    # subject/predicate que um usuario real do AIR usaria ao contar esse
    # fato pro sistema. Sentencas de recency_conflict COMPARTILHAM
    # subject+predicate de proposito (e' o unico jeito do mecanismo real
    # de recencia do AIR -- supersede por chave igual -- ser exercitado
    # de verdade; sem isso eu estaria testando uma versao capada do AIR,
    # nao o AIR de verdade). Outras abordagens (truncation, keyword,
    # semantic RAG) NAO recebem esse campo -- elas nao tem conceito de
    # chave estruturada, entao nao faria sentido dar a elas.
    sentence_keys: list[dict] = field(default_factory=list)


def _filler(rng: random.Random, n: int) -> list[str]:
    out = []
    for _ in range(n):
        t = rng.choice(FILLER_TEMPLATES)
        out.append(t.format(day=rng.choice(DAYS)))
    return out


def _make_factual_simple(rng: random.Random, idx: int) -> Case:
    service = rng.choice(SERVICES)
    owner = rng.choice(PEOPLE)
    fact = f"O servico {service} e' mantido pela equipe de {owner}."
    sentences = _filler(rng, rng.randint(2, 4))
    pos = rng.randint(0, len(sentences))
    sentences.insert(pos, fact)
    return Case(
        id=f"factual_simple_{idx:02d}", category="factual_simple", difficulty="easy",
        context=" ".join(sentences),
        question=f"Quem mantem o servico {service}?",
        expected_answer=owner,
        relevant_information=[fact],
        sentence_keys=[{"text": fact, "subject": f"service:{service}", "predicate": "maintainer"}],
    )


def _make_multi_hop(rng: random.Random, idx: int) -> Case:
    project = rng.choice(PROJECTS)
    person = rng.choice(PEOPLE)
    channel = rng.choice(CHANNELS)
    fact1 = f"O projeto {project} e' de responsabilidade de {person}."
    fact2 = f"{person} prefere ser contatado(a) por {channel}."
    sentences = _filler(rng, rng.randint(2, 4))
    # insere os dois fatos em posicoes DIFERENTES, nao adjacentes --
    # forca combinar informacao de partes distintas do contexto.
    sentences.insert(rng.randint(0, len(sentences)), fact1)
    sentences.insert(rng.randint(0, len(sentences)), fact2)
    return Case(
        id=f"multi_hop_{idx:02d}", category="multi_hop", difficulty="medium",
        context=" ".join(sentences),
        question=f"Qual o canal de contato preferido do responsavel pelo projeto {project}?",
        expected_answer=channel,
        relevant_information=[fact1, fact2],
        sentence_keys=[
            {"text": fact1, "subject": f"project:{project}", "predicate": "owner"},
            {"text": fact2, "subject": f"person:{person}", "predicate": "preferred_channel"},
        ],
    )


def _make_recency_conflict(rng: random.Random, idx: int) -> Case:
    project = rng.choice(PROJECTS)
    db_old = rng.choice(DATABASES)
    db_new = rng.choice([d for d in DATABASES if d != db_old])
    date_old = rng.choice(DATES_OLD)
    date_new = rng.choice(DATES_NEW)
    fact_old = f"Em {date_old}, o time decidiu usar {db_old} para o projeto {project}."
    fact_new = f"Em {date_new} (mais recente), o projeto {project} foi migrado para {db_new}."
    sentences = _filler(rng, rng.randint(1, 3))
    sentences.insert(0, fact_old)
    sentences.append(fact_new)  # a versao correta vem por ULTIMO, mas o teste real e' se a abordagem entende recencia, nao so' posicao
    return Case(
        id=f"recency_conflict_{idx:02d}", category="recency_conflict", difficulty="hard",
        context=" ".join(sentences),
        question=f"Qual banco de dados o projeto {project} usa atualmente?",
        expected_answer=db_new,
        relevant_information=[fact_new],
        # MESMO subject+predicate nos dois -- e' o que faz o AIR
        # realmente exercitar supersede/recencia aqui, nao so' guardar
        # dois fatos soltos. Ordem da lista = ordem de insercao (fact_old
        # PRIMEIRO), que e' o que determina qual supersede qual no AIR --
        # independente de onde cada frase cai no texto do 'context'.
        sentence_keys=[
            {"text": fact_old, "subject": f"project:{project}", "predicate": "database"},
            {"text": fact_new, "subject": f"project:{project}", "predicate": "database"},
        ],
    )


def _make_long_distance(rng: random.Random, idx: int) -> Case:
    service = rng.choice(SERVICES)
    owner = rng.choice(PEOPLE)
    fact = f"O servico {service} e' mantido pela equipe de {owner}."
    n_filler = rng.randint(35, 50)
    sentences = _filler(rng, n_filler)
    # posicao controlada: idx par = perto do inicio (facil), idx impar = no meio (dificil)
    if idx % 2 == 0:
        pos = rng.randint(0, 3)
        difficulty = "medium"
    else:
        pos = len(sentences) // 2 + rng.randint(-3, 3)
        difficulty = "hard"
    pos = max(0, min(pos, len(sentences)))
    sentences.insert(pos, fact)
    return Case(
        id=f"long_distance_{idx:02d}", category="long_distance", difficulty=difficulty,
        context=" ".join(sentences),
        question=f"Quem mantem o servico {service}?",
        expected_answer=owner,
        relevant_information=[fact],
        sentence_keys=[{"text": fact, "subject": f"service:{service}", "predicate": "maintainer"}],
    )


def _make_irrelevant_context(rng: random.Random, idx: int) -> Case:
    target_service = rng.choice(SERVICES)
    target_owner = rng.choice(PEOPLE)
    target_fact = f"O servico {target_service} e' mantido pela equipe de {target_owner}."

    distractor_services = rng.sample([s for s in SERVICES if s != target_service], k=min(4, len(SERVICES) - 1))
    distractor_facts = []
    distractor_keys = []
    used_people = {target_owner}
    for s in distractor_services:
        p = rng.choice([x for x in PEOPLE if x not in used_people])
        used_people.add(p)
        f = f"O servico {s} e' mantido pela equipe de {p}."
        distractor_facts.append(f)
        distractor_keys.append({"text": f, "subject": f"service:{s}", "predicate": "maintainer"})

    all_facts = distractor_facts + [target_fact]
    rng.shuffle(all_facts)
    return Case(
        id=f"irrelevant_context_{idx:02d}", category="irrelevant_context", difficulty="hard",
        context=" ".join(all_facts),
        question=f"Quem mantem o servico {target_service}?",
        expected_answer=target_owner,
        relevant_information=[target_fact],
        sentence_keys=distractor_keys + [{"text": target_fact, "subject": f"service:{target_service}", "predicate": "maintainer"}],
    )


def _make_repeated_information(rng: random.Random, idx: int) -> Case:
    service = rng.choice(SERVICES)
    owner = rng.choice(PEOPLE)
    fact = f"O servico {service} e' mantido pela equipe de {owner}."
    rephrasings = [
        fact,
        f"Como ja' mencionado, {owner} e' quem cuida do {service}.",
        f"Reforcando: a manutencao do {service} e' responsabilidade de {owner}.",
    ]
    n_repeats = rng.randint(3, 5)
    sentences = _filler(rng, rng.randint(2, 4))
    occurrences = []
    for i in range(n_repeats):
        occ = rephrasings[i % len(rephrasings)]
        occurrences.append(occ)
        sentences.insert(rng.randint(0, len(sentences)), occ)
    return Case(
        id=f"repeated_information_{idx:02d}", category="repeated_information", difficulty="medium",
        context=" ".join(sentences),
        question=f"Quem mantem o servico {service}?",
        expected_answer=owner,
        relevant_information=occurrences,
        # todas as repeticoes compartilham subject+predicate DE PROPOSITO
        # -- ingerido no AIR, isso colapsa em UM fato ativo (as
        # repeticoes anteriores viram SUPERSEDED), testando exatamente a
        # hipotese da categoria: reducao de redundancia por chave, nao
        # por comprimir texto.
        sentence_keys=[{"text": occ, "subject": f"service:{service}", "predicate": "maintainer"} for occ in occurrences],
    )


CATEGORY_BUILDERS = {
    "factual_simple": _make_factual_simple,
    "multi_hop": _make_multi_hop,
    "recency_conflict": _make_recency_conflict,
    "long_distance": _make_long_distance,
    "irrelevant_context": _make_irrelevant_context,
    "repeated_information": _make_repeated_information,
}


def build_dataset(n_per_category: int = 8, seed: int = SEED) -> list[Case]:
    rng = random.Random(seed)
    cases = []
    for category, builder in CATEGORY_BUILDERS.items():
        for i in range(n_per_category):
            cases.append(builder(rng, i))
    return cases


def main():
    cases = build_dataset()
    out_path = Path(__file__).parent / "dataset.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump([asdict(c) for c in cases], f, ensure_ascii=False, indent=2)
    print(f"{len(cases)} casos gerados em {out_path}")
    by_cat = {}
    for c in cases:
        by_cat.setdefault(c.category, 0)
        by_cat[c.category] += 1
    for cat, n in by_cat.items():
        print(f"  {cat}: {n}")


if __name__ == "__main__":
    main()
