Passei os últimos dias construindo o AIR (AI Runtime) — uma camada de
infraestrutura pra agentes/LLM baseada num princípio simples:

> Informação persistente e estruturada não deveria ser reenviada como
> tokens a cada turno de conversa.

A maioria dos "agent frameworks" reenvia o histórico inteiro (ou um
resumo dele) toda vez. O AIR separa isso em três peças: World State
(entidade/relação/evento consultável por query direta), Memory (fatos
discretos com recência explícita) e um Context Engine que transforma
output grande de tool num handle curto — só entra inteiro no prompt se
for pequeno ou explicitamente fixado.

Medido com tokenizador real (não estimativa), numa sessão sintética de
30 turnos investigando uma queda de serviço: 98.5% de economia de
tokens comparado a reenviar o histórico completo a cada turno.

O que eu quero destacar não é o número — é como cheguei nele. Ao
publicar o projeto, testei o servidor MCP (a peça que deixa o Claude
Code consultar o AIR como memória externa) e uma chamada real levou
208 segundos pra responder. Parecia travado. Não estava: era o
carregamento do tokenizer na primeira chamada de cada processo —
depois disso, a mesma chamada leva 2.9 milissegundos. Documentei isso
no README como limitação conhecida, não escondi.

Prefiro publicar um projeto com os problemas reais visíveis a publicar
um "funciona sempre" que não é verdade. O repositório inteiro segue
essa disciplina: cada peça tem uma seção explícita de "o que é real
vs. o que é simulado", sem number inflado.

Repo: [link depois de publicar]

#AI #LLM #EngenhariaDeSoftware #OpenSource
