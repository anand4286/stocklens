"""Blend the families into one score, and show the arithmetic.

    score      = sum(w_i * s_i) / sum(w_i)       over available directional families
    coverage   = sum(w_i available) / sum(w_i)   over all directional families
    conviction = mean(volume, news_flow)         over available conviction families
    confidence = coverage * (1 + 0.25 * conviction), clamped to [0, 1]

Unavailable families leave both numerator and denominator, so the composite
stays on [-1, +1] however many sources answered; the missing ones are paid for
in `confidence`, not hidden in the score.

The stance labels are descriptive, not advice. They are thresholds on the
composite, and the thresholds are on the page next to them.
"""

from __future__ import annotations

from dataclasses import dataclass

from .signals import Signal, clamp

#: Picked, not fitted. The open edition ships transparent defaults; the
#: evaluation harness (`stocklens-evaluate`) is how to earn different ones on
#: your own data. Price-derived families carry the most weight because they
#: are available for every symbol every day.
DEFAULT_WEIGHTS: dict[str, float] = {
    "technical": 0.20,
    "momentum": 0.20,
    "relative_strength": 0.15,
    "fundamental": 0.20,
    "valuation": 0.10,
    "sentiment": 0.15,
}
CONSTRUCTIVE_AT = 0.20
CAUTIOUS_AT = -0.20
MIN_CONFIDENCE = 0.45


@dataclass(frozen=True, slots=True)
class Composite:
    score: float
    confidence: float
    coverage: float
    conviction: float | None
    stance: str
    contributions: list[dict]

    def as_dict(self) -> dict:
        return {
            "score": round(self.score, 3),
            "confidence": round(self.confidence, 3),
            "coverage": round(self.coverage, 3),
            "conviction": None if self.conviction is None else round(self.conviction, 3),
            "stance": self.stance,
            "rule": {
                "constructive_at": CONSTRUCTIVE_AT,
                "cautious_at": CAUTIOUS_AT,
                "min_confidence": MIN_CONFIDENCE,
            },
            "contributions": self.contributions,
        }


def blend(signals: list[Signal], weights: dict[str, float] | None = None) -> Composite:
    weights = weights or DEFAULT_WEIGHTS
    directional = [s for s in signals if s.kind == "directional"]
    available = [s for s in directional if s.available]
    # Coverage is measured against the whole model, so a family that was not
    # even attempted counts as missing evidence, the same as one that abstained.
    total_w = sum(weights.values()) or 1.0
    used_w = sum(weights.get(s.name, 0) for s in available)
    score = sum(weights.get(s.name, 0) * s.score for s in available) / used_w if used_w else 0.0
    coverage = used_w / total_w

    conv = [s.score for s in signals if s.kind == "conviction" and s.available]
    conviction = sum(conv) / len(conv) if conv else None
    confidence = clamp(coverage * (1 + 0.25 * (conviction or 0)), 0.0, 1.0)

    if confidence < MIN_CONFIDENCE:
        stance = "Insufficient evidence"
    elif score >= CONSTRUCTIVE_AT:
        stance = "Constructive"
    elif score <= CAUTIOUS_AT:
        stance = "Cautious"
    else:
        stance = "Neutral"

    contributions = []
    for s in signals:
        w = weights.get(s.name, 0.0) if s.kind == "directional" else 0.0
        effective = w / used_w if s.available and used_w and w else 0.0
        part = (s.score or 0.0) * effective
        contributions.append({
            "name": s.name,
            "kind": s.kind,
            "score": None if s.score is None else round(s.score, 3),
            "weight": w,
            "effective_weight": round(effective, 4),
            # These sum to the composite score, so every point of it is attributable.
            "contribution": round(part, 4),
            "reason": s.reason,
            "detail": s.detail,
        })
    return Composite(score, confidence, coverage, conviction, stance, contributions)
