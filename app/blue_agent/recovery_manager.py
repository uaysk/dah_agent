from __future__ import annotations

from typing import Any


def recovery_converged(truth: dict[str, Any]) -> bool:
    return (
        not truth.get("active_injections")
        and not truth.get("quarantined", False)
        and truth.get("command_delivered_count") == truth.get("command_sent_count")
        and truth.get("telemetry_fresh", False)
    )
