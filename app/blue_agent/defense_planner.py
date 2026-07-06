from __future__ import annotations


def defense_sequence(classification: str) -> list[str]:
    if classification in {"ATTACK_SUSPECTED", "ATTACK_CONFIRMED", "UNCERTAIN"}:
        return [
            "increase_monitoring_level",
            "mark_telemetry_untrusted",
            "quarantine_link_session",
            "request_state_resynchronization",
        ]
    return []
