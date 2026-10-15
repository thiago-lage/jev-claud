"""Decisões tipadas e política de confiança.

Uma `Decision` é o que o código consome para ramificar: um valor tipado (rótulo, booleano
ou grau), as probabilidades, a confiança e uma faixa (`Band`) já calculada pela `Policy`.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

Kind = Literal["choice", "noul", "score", "box"]
Source = Literal["jev", "offline", "cache", "mixed"]
"""De onde veio a resposta. `mixed`: na cascata, parte offline e parte no Jev."""


class Band(str, Enum):
    """Faixa de confiança: agir sozinho, confirmar antes, ou escalar (humano/fallback)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class Policy:
    """Limiares de confiança. Ajuste ao custo de errar no seu domínio.

    - confiança >= `act`: `Band.HIGH` (pode agir automaticamente)
    - confiança >= `confirm`: `Band.MEDIUM` (agir com cautela ou confirmar)
    - abaixo disso: `Band.LOW` (usar o caminho padrão/humano)
    """

    act: float = 0.75
    confirm: float = 0.5

    def __post_init__(self) -> None:
        if not 0.0 <= self.confirm <= self.act <= 1.0:
            raise ValueError("Policy exige 0 <= confirm <= act <= 1.")

    def band(self, confidence: float) -> Band:
        if confidence >= self.act:
            return Band.HIGH
        if confidence >= self.confirm:
            return Band.MEDIUM
        return Band.LOW


# ---------------------------------------------------------------------------
# Fórmulas de confiança (as mesmas da documentação do TypeSafe, docs.typesafe.ai/confidence)
# ---------------------------------------------------------------------------


def choice_confidence(probabilities: Mapping[str, float]) -> float:
    """(p_max - 1/n) / (1 - 1/n): 1 quando uma opção leva tudo, 0 numa divisão uniforme."""
    n = len(probabilities)
    if n <= 1:
        return 1.0
    p_max = max(probabilities.values())
    return _clamp((p_max - 1.0 / n) / (1.0 - 1.0 / n))


def score_confidence(probabilities: Mapping[int, float]) -> float:
    """1 - desvio médio absoluto em torno do nível mais provável, normalizado pela distribuição uniforme."""
    n = len(probabilities)
    if n <= 1:
        return 1.0
    mode = max(probabilities, key=lambda level: probabilities[level])
    mad = sum(p * abs(level - mode) for level, p in probabilities.items())
    mad_uniform = sum(abs(i - (n - 1) / 2) for i in range(n)) / n
    return _clamp(1.0 - mad / mad_uniform) if mad_uniform else 1.0


def noul_confidence(p_yes: float) -> float:
    """|2p - 1|: 0 em 0,5 (incerto), 1 em 0 ou 1."""
    return _clamp(abs(2.0 * p_yes - 1.0))


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Region:
    """Retângulo numa imagem, com as bordas em frações da altura/largura (0 a 1)."""

    top: float
    left: float
    bottom: float
    right: float

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)

    @property
    def area(self) -> float:
        """Fração da imagem coberta (0 a 1)."""
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        """(x, y) do centro, em frações."""
        return ((self.left + self.right) / 2, (self.top + self.bottom) / 2)

    def to_pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        """(x0, y0, x1, y1) em pixels, no formato do Pillow (`ImageDraw.rectangle`, `crop`)."""
        return (
            round(self.left * width),
            round(self.top * height),
            round(self.right * width),
            round(self.bottom * height),
        )

    def as_dict(self) -> dict[str, float]:
        return {"top": self.top, "left": self.left, "bottom": self.bottom, "right": self.right}


@dataclass(frozen=True)
class Decision:
    """Uma resposta tipada pronta para `if`/`match`.

    - choice: `value` é o rótulo escolhido (str)
    - noul: `value` é `p > 0.5` (bool; sem evidência = não) e `probability` é p(sim)
    - score: `value` é o grau esperado (float, pode cair entre níveis) e `level` o nível mais provável
    - box: `value` é uma `Region`; sem confiança informada pelo modelo, a faixa é MEDIUM
    """

    name: str
    kind: Kind
    value: str | bool | float | Region
    confidence: float
    band: Band
    probabilities: Mapping[Any, float]
    source: Source
    model: str | None = None
    probability: float | None = None
    level: int | None = None
    legend: Mapping[int, Any] = field(default_factory=dict)

    @property
    def yes(self) -> bool:
        """Para Noul: a condição é mais provável que não. Para outros tipos, levanta erro."""
        if self.kind != "noul":
            raise TypeError(f"Decision {self.name!r} é {self.kind}, não noul.")
        return bool(self.value)

    @property
    def confident(self) -> bool:
        """Atalho: `band` é HIGH."""
        return self.band is Band.HIGH

    @property
    def uncertain(self) -> bool:
        """Atalho: `band` é LOW."""
        return self.band is Band.LOW

    def is_(self, label: str) -> bool:
        """Para Choice: `True` se o rótulo escolhido é `label`."""
        if self.kind != "choice":
            raise TypeError(f"Decision {self.name!r} é {self.kind}, não choice.")
        return self.value == label

    def at_least(self, level: float) -> bool:
        """Para Score: `True` se o grau esperado é >= `level`."""
        if self.kind != "score":
            raise TypeError(f"Decision {self.name!r} é {self.kind}, não score.")
        return self.number >= level

    @property
    def number(self) -> float:
        """O valor como número (score: grau esperado; noul: p(sim); choice: probabilidade escolhida)."""
        if isinstance(self.value, Region):
            raise TypeError(f"Decision {self.name!r} é box: use `region`.")
        if isinstance(self.value, str):
            return float(self.probability or 0.0)
        if self.kind == "noul":
            return float(self.probability if self.probability is not None else self.value)
        return float(self.value)

    @property
    def region(self) -> Region:
        """Para Box: a região encontrada."""
        if not isinstance(self.value, Region):
            raise TypeError(f"Decision {self.name!r} é {self.kind}, não box.")
        return self.value

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "value": self.value.as_dict() if isinstance(self.value, Region) else self.value,
            "confidence": round(self.confidence, 6),
            "band": self.band.value,
            "probability": self.probability,
            "level": self.level,
            "probabilities": {str(k): round(v, 6) for k, v in self.probabilities.items()},
            "source": self.source,
            "model": self.model,
        }


@dataclass(frozen=True)
class Decisions(Mapping[str, Decision]):
    """Todas as decisões de uma chamada, indexadas pelo nome da pergunta."""

    items_: Mapping[str, Decision]
    source: Source
    model: str | None = None
    input_tokens: int | None = None
    cost_usd: float = 0.0
    escalated: tuple[str, ...] = ()
    """Na cascata: perguntas que o offline não resolveu com segurança e foram ao Jev."""

    def __getitem__(self, name: str) -> Decision:
        return self.items_[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self.items_)

    def __len__(self) -> int:
        return len(self.items_)

    @property
    def cached(self) -> bool:
        return self.source == "cache"

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "cost_usd": self.cost_usd,
            "escalated": list(self.escalated),
            "decisions": {name: decision.as_dict() for name, decision in self.items_.items()},
        }
