from __future__ import annotations

from typing import Any


def summarize_verdict(verdict: dict[str, Any]) -> dict[str, Any]:
    return {
        "attack_score": verdict.get("attack_score", 0),
        "defense_score": verdict.get("defense_score", 0),
        "availability": verdict.get("availability", 0),
        "total_score": verdict.get("total_score", 0),
        "final_verdict": (verdict.get("reason") or {}).get("judge_audit_event", {}).get("final_verdict"),
    }
