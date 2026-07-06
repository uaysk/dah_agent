from __future__ import annotations


def is_coordinate_fresh(age_seconds: int | None, policy_seconds: int = 5) -> bool:
    return age_seconds is not None and age_seconds <= policy_seconds
