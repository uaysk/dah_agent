from __future__ import annotations

from typing import Any


def summarize_baseline_observation(observation: dict[str, Any]) -> dict[str, Any]:
    return {
        "flow_id": observation.get("target_flow_id", "flow-return-042"),
        "mission_phase": observation.get("mission_phase", "survey"),
        "sequence_pattern": observation.get("sequence_pattern", "monotonic"),
    }
