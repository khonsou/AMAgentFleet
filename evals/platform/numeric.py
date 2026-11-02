"""Deterministic arithmetic checks for multi-agent evidence assembly."""

from __future__ import annotations


class NumericCheckError(ValueError):
    """Raised when independently reported facts do not reconcile."""


def require_conservation(total: int, parts: list[int], *, expected_total: int | None = None) -> int:
    computed = sum(parts)
    expected = computed if expected_total is None else expected_total
    if computed != expected:
        raise NumericCheckError(f"Conservation mismatch: sum(parts)={computed}, expected={expected}")
    return computed


def percent(part: int, total: int, *, digits: int = 2) -> float:
    if total <= 0:
        raise NumericCheckError("total must be positive")
    return round(part / total * 100, digits)


def reconcile_ratio(part: int, total: int, reported: float, *, digits: int = 2, tolerance: float = 0.0) -> float:
    computed = percent(part, total, digits=digits)
    if abs(computed - reported) > tolerance:
        raise NumericCheckError(f"Ratio mismatch: computed={computed}, reported={reported}")
    return computed
