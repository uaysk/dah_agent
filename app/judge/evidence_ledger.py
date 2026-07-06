from __future__ import annotations

from typing import Any

from ..models import new_id


def build_evidence_ledger(response: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    mission_summary = (response.get("mission_simulation") or {}).get("summary") or {}
    judge_result = response.get("judge_verdict", {})
    for event in events:
        payload = event.get("payload") or {}
        event_type = str(event.get("event_type", ""))
        record = {
            "evidence_id": new_id("evidence"),
            "event_id": event.get("event_id"),
            "incident_id": event.get("incident_id") or response.get("incident_id"),
            "evidence_type": event_type,
            "observed_fact": _observed_fact(event_type, payload),
            "supporting_evidence": _supporting(event_type, payload),
            "contradicting_evidence": _contradicting(event_type, payload),
            "selected_action": _selected_action(event_type, payload),
            "policy_result": payload.get("policy_result"),
            "tool_result": _tool_result(event_type, payload),
            "verification_result": _verification_result(event_type, payload),
            "mission_impact": _mission_impact(event_type, payload, mission_summary),
            "judge_result": judge_result if event_type == "judge_verdict" else None,
            "supporting": event_type not in {"tool_denied", "tool_duplicate"},
            "contradicting": False,
            "confidence": _confidence(event_type, payload),
            "occurred_at": event.get("occurred_at"),
            "source": event.get("source"),
        }
        records.append(record)
    return records


def _observed_fact(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == "command_delivery_anomaly":
        return "command or coordinate report delivery gap observed"
    if event_type == "classification_completed":
        return f"blue classification={payload.get('classification')}"
    if event_type == "impact_verified":
        return "impact verification completed"
    if event_type == "recovery_verified":
        return "recovery verification completed"
    if event_type == "judge_verdict":
        return "independent judge verdict recorded"
    return event_type.replace("_", " ")


def _supporting(event_type: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for key in (
        "command_gap",
        "target_drop_rate",
        "max_consecutive_gap_seconds",
        "fault_profile",
        "classification",
        "attack_score",
        "fault_score",
        "success",
    ):
        if key in payload:
            refs.append({"field": key, "value": payload[key]})
    if payload.get("result") and isinstance(payload["result"], dict):
        for key in ("command_gap", "max_consecutive_gap_seconds", "target_drop_rate", "technical_success"):
            if key in payload["result"]:
                refs.append({"field": f"result.{key}", "value": payload["result"][key]})
    return refs


def _contradicting(event_type: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    if event_type == "classification_completed" and payload.get("fault_score", 0) >= payload.get("attack_score", 0):
        return [{"field": "fault_score", "value": payload.get("fault_score")}]
    return []


def _selected_action(event_type: str, payload: dict[str, Any]) -> str | None:
    if event_type in {"red_plan_created", "tool_executed", "tool_denied", "tool_duplicate"}:
        return payload.get("tool_name")
    return None


def _tool_result(event_type: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    if event_type in {"tool_executed", "tool_denied", "tool_duplicate"}:
        return {
            "tool_name": payload.get("tool_name"),
            "accepted": payload.get("accepted"),
            "status": payload.get("status"),
            "result": payload.get("result"),
        }
    return None


def _verification_result(event_type: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    if event_type in {"impact_verified", "recovery_verified"}:
        return {"success": payload.get("success"), "result": payload.get("result")}
    return None


def _mission_impact(event_type: str, payload: dict[str, Any], mission_summary: dict[str, Any]) -> dict[str, Any] | None:
    if event_type in {"impact_verified", "judge_verdict", "command_delivery_anomaly", "safe_containment_entered"}:
        return {
            "safe_stop_triggered": mission_summary.get("safe_stop_triggered"),
            "safe_stop_second": mission_summary.get("safe_stop_second"),
            "ugv_final_state": mission_summary.get("ugv_final_state"),
            "max_consecutive_coordinate_gap_seconds": mission_summary.get("max_consecutive_coordinate_gap_seconds"),
        }
    return None


def _confidence(event_type: str, payload: dict[str, Any]) -> float:
    if event_type == "judge_verdict":
        return 1.0
    if event_type in {"impact_verified", "recovery_verified"}:
        return 0.9 if payload.get("success") else 0.6
    if event_type == "classification_completed":
        return round(max(float(payload.get("attack_score", 0)), float(payload.get("fault_score", 0))), 2)
    return 0.75
