from __future__ import annotations

from typing import Literal

Classification = Literal["WATCH", "FAULT_SUSPECTED", "ATTACK_SUSPECTED", "ATTACK_CONFIRMED", "UNCERTAIN"]


def classify_scores(attack_score: float, fault_score: float) -> Classification:
    if attack_score >= 0.85:
        return "ATTACK_CONFIRMED"
    if attack_score >= 0.70:
        return "ATTACK_SUSPECTED"
    if fault_score >= 0.70:
        return "FAULT_SUSPECTED"
    if abs(attack_score - fault_score) < 0.15:
        return "UNCERTAIN"
    return "WATCH"
