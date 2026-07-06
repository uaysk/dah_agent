from __future__ import annotations

from typing import Any


def score_hypothesis(hypothesis: str, observation: dict[str, Any]) -> float:
    base = {
        "H1_coordinate_report_suppression": 0.82,
        "H2_display_replay_effect": 0.72,
        "H3_recovery_interference": 0.70,
        "H0_baseline_or_fault": 0.2,
    }.get(hypothesis, 0.0)
    if observation.get("sequence_gap"):
        base += 0.08
    return min(base, 1.0)
