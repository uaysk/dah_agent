from __future__ import annotations


def command_gap(sent: int, delivered: int) -> int:
    return max(0, sent - delivered)
