from __future__ import annotations

from typing import Any


def select_target_flow(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    return max(candidates, key=lambda item: float(item.get("uav_to_ugv_relevance", 0)))
