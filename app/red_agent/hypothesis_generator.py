from __future__ import annotations


def generate_hypotheses(attack_type: str) -> list[str]:
    mapping = {
        "selective_message_drop": ["H1_coordinate_report_suppression"],
        "selective_command_drop": ["H1_coordinate_report_suppression"],
        "display_replay_effect": ["H2_display_replay_effect"],
        "recovery_interference": ["H3_recovery_interference"],
    }
    return mapping.get(attack_type, ["H0_baseline_or_fault"])
