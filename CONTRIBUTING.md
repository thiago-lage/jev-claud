# Contribuindo

Obrigado por ajudar! O projeto segue três princípios:

1. **O código decide com Jev, a pessoa lê com LLM.** Nada de pedir JSON para uma LLM e fazer
   parse quando uma decisão tipada resolve.
2. **Custo zero por padrão.** Nenhuma mudança pode fazer o jev-claude gastar dinheiro sem que o
   usuário tenha configurado um orçamento.
3. **Núcleo sem dependências.** O modo offline, o cache e a escrita via LLM usam só a stdlib.
   O `typesafe-sdk` é opcional (`jev-claude[jev]`).

## Ambiente

```bash
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Antes de abrir um PR

```bash
ruff format .
ruff check .
mypy
pytest --cov
```

- Testes não podem usar rede externa nem gastar créditos: simule o Jev com `FakeClient`
  (`tests/conftest.py`), que devolve os tipos de resposta reais do SDK.
- Mudou comportamento visível? Atualize o `README.md` e o `CHANGELOG.md`.
- Integração com o Jev: confira a documentação ao vivo em https://docs.typesafe.ai/llms.txt
  antes de assumir detalhes da API; ela é a fonte da verdade.
