"""Orçamento mensal para chamadas pagas ao Jev.

Padrão: US$ 0 (custo zero). Sem orçamento, o ramifica responde com o backend offline e
avisa uma vez no log. Defina `RAMIFICA_BUDGET_USD` (ex.: `1.00`) para liberar o Jev.

Preço de referência (docs.typesafe.ai/models, 2026-10): US$ 0,042 por milhão de tokens de
entrada; tokens de saída são grátis. Ajuste com `RAMIFICA_JEV_USD_PER_MTOK` se mudar.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ramifica.errors import BudgetExceeded

USD_PER_MTOK = 0.042
# Uma requisição mínima de uma pergunta mediu ~300 tokens de entrada nos exemplos da API:
# há um custo fixo por requisição além do texto. A estimativa é conservadora de propósito.
REQUEST_OVERHEAD_TOKENS = 300
QUESTION_OVERHEAD_TOKENS = 40
CHARS_PER_TOKEN = 3.0


@dataclass(frozen=True)
class Budget:
    monthly_usd: float = 0.0
    usd_per_mtok: float = USD_PER_MTOK

    @property
    def zero_cost(self) -> bool:
        return self.monthly_usd <= 0

    def cost(self, input_tokens: int) -> float:
        return input_tokens * self.usd_per_mtok / 1_000_000

    def estimate_tokens(self, state: Any, wire_questions: Mapping[str, Any]) -> int:
        chars = len(
            json.dumps({"state": state, "questions": wire_questions}, ensure_ascii=False, default=str)
        )
        return (
            REQUEST_OVERHEAD_TOKENS
            + QUESTION_OVERHEAD_TOKENS * len(wire_questions)
            + math.ceil(chars / CHARS_PER_TOKEN)
        )

    def check(self, estimated_tokens: int, spent_usd: float) -> None:
        estimated = self.cost(estimated_tokens)
        if spent_usd + estimated > self.monthly_usd:
            raise BudgetExceeded(estimated, spent_usd, self.monthly_usd)
