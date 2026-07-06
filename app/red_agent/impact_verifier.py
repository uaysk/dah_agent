from __future__ import annotations

from typing import Any


def red_visible_impact_summary(verification: dict[str, Any]) -> dict[str, Any]:
    result = verification.get("result") or {}
    return {
        "technical_success": result.get("technical_success", False),
        "max_consecutive_gap_seconds": result.get("max_consecutive_gap_seconds", 0),
        "target_drop_rate": result.get("target_drop_rate", 0),
    }
