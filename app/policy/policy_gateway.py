from __future__ import annotations

from typing import Any

from .safety_invariant import violates_safety_boundary

RISK_ORDER = {"A0": 0, "A1": 1, "A2": 2, "A3": 3, "A4": 4}
POLICY_VERSION = "policy-gateway-v1"


def evaluate_action(tool_name: str, risk_level: str, target: dict[str, Any] | None = None) -> dict[str, Any]:
    target = target or {}
    session_id = str(target.get("session_id", ""))
    target_values = [str(value) for value in target.values() if value is not None]
    boundary_hits = [value for value in target_values if violates_safety_boundary(value)]

    decision = {
        "policy_version": POLICY_VERSION,
        "tool_name": tool_name,
        "risk_level": risk_level,
        "target_session_id": session_id or None,
        "allowed": False,
        "reason": "",
        "checked_boundaries": ["risk_level", "target_allowlist", "safety_invariant"],
    }
    if boundary_hits:
        decision.update({"reason": "safety_boundary_violation", "boundary_hits": boundary_hits})
        return decision
    if RISK_ORDER.get(risk_level, 4) >= RISK_ORDER["A4"]:
        decision.update({"reason": "risk_a4_always_denied"})
        return decision
    if tool_name == "restore_attack_injection_state":
        allowed = bool(target.get("injection_id"))
        decision.update({"allowed": allowed, "reason": "allowlisted_injection_restore" if allowed else "missing_injection_id"})
        return decision
    allowed = session_id.startswith("session-")
    decision.update({"allowed": allowed, "reason": "allowlisted_session" if allowed else "target_not_allowlisted"})
    return decision


def action_allowed(tool_name: str, risk_level: str, session_id: str) -> bool:
    return bool(evaluate_action(tool_name, risk_level, {"session_id": session_id}).get("allowed"))
