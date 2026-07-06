from __future__ import annotations

FORBIDDEN_TARGETS = {"real_uav", "real_ugv", "rf", "satellite", "shell", "sql", "cloud_iam"}


def violates_safety_boundary(target: str) -> bool:
    normalized = target.lower().replace("-", "_")
    return any(item in normalized for item in FORBIDDEN_TARGETS)
