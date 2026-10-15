# jev-claud — guia para agentes

- Python >= 3.10, layout `src/`. Núcleo sem dependências; `typesafe-sdk` só no extra `[jev]`.
- Verificação completa: `ruff format . && ruff check . && mypy && pytest --cov`.
  Código com caminhos por sistema: rode também `mypy --platform linux` e `--platform darwin`.
- O assistente (`src/jev_claud/wizard.py`) toca o sistema só via `System`; nos testes use
  `FakeSystem` (`tests/test_wizard.py`). Instaladores: `install.ps1` e `install.sh` (CI testa os dois).
- Testes nunca usam rede externa nem gastam créditos: o Jev é simulado por `FakeClient`
  em `tests/conftest.py`; a LLM de texto por um `HTTPServer` local.
- Regra do projeto: saída que o código ramifica -> `decide/when/switch/rank` (Jev);
  texto que uma pessoa lê -> `write` (LLM). Padrão de orçamento é US$ 0.
- Antes de mudar a integração com o Jev, leia https://docs.typesafe.ai/llms.txt e use a
  skill `typesafe:typesafe-ai`; confira os tipos reais em `typesafe_sdk` instalado.
- Mensagens, docstrings e docs em português do Brasil.
