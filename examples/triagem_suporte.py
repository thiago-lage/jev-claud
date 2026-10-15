"""Triagem de tickets: o código decide com Jev, o cliente lê o texto da LLM.

Rode sem configurar nada (modo offline, custo zero):

    python examples/triagem_suporte.py

Com Jev (pago, com teto):  TYPESAFE_API_KEY=... RAMIFICA_BUDGET_USD=1 python examples/triagem_suporte.py
Com texto via Ollama local: instale https://ollama.com e rode `ollama pull llama3.2`.
"""

from __future__ import annotations

from ramifica import Band, NoWriterError, Policy, Ramifica, choice, noul, score

TICKETS = [
    "Fui cobrado duas vezes na fatura deste mês. Quero o reembolso hoje!",
    "O aplicativo dá erro ao abrir a tela de pagamento, aparece uma falha técnica.",
    "Quero saber o preço do plano anual para fazer upgrade.",
    "Bom dia.",
]

QUESTIONS = {
    "setor": choice(
        "Qual setor deve atender este ticket?",
        {
            "financeiro": "cobrança, fatura, reembolso, pagamento duplicado",
            "suporte": "erro, bug, falha técnica, não funciona",
            "vendas": "compra, plano, preço, upgrade",
            "nenhum": "saudação ou mensagem sem pedido claro",
        },
    ),
    "urgente": noul(
        "O cliente pede ação imediata?",
        yes="urgente, hoje, agora, imediato",
        no="sem pressa, quando puder, só uma dúvida",
    ),
    "irritacao": score(
        "Quão irritado está o cliente?",
        ["calmo, cordial", "incomodado, chateado", "furioso, exige, absurdo"],
    ),
}


def rotear(r: Ramifica, ticket: str) -> str:
    # Uma requisição, três decisões independentes avaliadas em paralelo.
    d = r.decide(ticket, QUESTIONS)
    setor, urgente = d["setor"], d["urgente"]

    # Política explícita em código: incerteza vai para um humano.
    if setor.band is Band.LOW or setor.is_("nenhum"):
        destino = "fila humana"
    elif urgente.yes and urgente.confident:
        destino = f"{setor.value} (prioridade)"
    else:
        destino = str(setor.value)

    print(f"\n> {ticket}")
    print(
        f"  setor={setor.value} ({setor.confidence:.2f}, {setor.band.value})  "
        f"urgente={urgente.yes} (p={urgente.probability:.2f})  "
        f"irritação={float(d['irritacao'].value):.1f}  via {d.source}"
    )
    print(f"  destino: {destino}")
    return destino


def main() -> None:
    with Ramifica(policy=Policy(act=0.7, confirm=0.4)) as r:
        for ticket in TICKETS:
            destino = rotear(r, ticket)
            try:
                # Texto que uma pessoa vai ler: aqui sim, LLM.
                resposta = r.write(
                    "Escreva uma resposta curta e empática ao cliente, em português.",
                    context={"ticket": ticket, "encaminhado_para": destino},
                    max_tokens=120,
                )
                print(f"  resposta: {resposta}")
            except NoWriterError:
                print("  resposta: (sem LLM local; instale o Ollama para gerar texto grátis)")


if __name__ == "__main__":
    main()
