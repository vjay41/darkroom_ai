"""Edit limits: every requested change is clamped and the result says what was asked and applied.

Three limits apply to a slider, in this order:
1. per pass - one tool call may move the value by at most `per_pass`;
2. total    - the value may differ from the photo's ORIGINAL value by at most `total`
              (only if a total is set; exceeding it needs an explicit confirmation);
3. range    - the slider's own minimum and maximum.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

DECIMALS = 4


@dataclass(frozen=True, slots=True)
class Limit:
    name: str  # friendly name used by the tools, e.g. "exposure"
    key: str  # Lightroom develop setting, e.g. "Exposure2012"
    minimum: float
    maximum: float
    per_pass: float
    total: float | None = None


class ClampedChange(BaseModel):
    """One slider change: what was requested, what was applied, and why they differ."""

    model_config = ConfigDict(frozen=True)

    name: str
    key: str
    requested_change: float
    applied_change: float
    before: float
    after: float
    original: float
    limited_by: tuple[str, ...] = ()  # any of "per_pass", "total", "range"

    @property
    def clamped(self) -> bool:
        return bool(self.limited_by)


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def clamp_change(
    limit: Limit,
    *,
    current: float,
    original: float,
    requested_change: float,
    allow_beyond_total: bool = False,
) -> ClampedChange:
    """Apply the per-pass, total and range limits to a requested change of one slider."""
    reasons: list[str] = []

    change = _clip(requested_change, -limit.per_pass, limit.per_pass)
    if change != requested_change:
        reasons.append("per_pass")
    target = current + change

    if limit.total is not None and not allow_beyond_total:
        # If the value is already outside the total band (after a confirmed override), moving
        # back towards the original is still allowed; moving further away is not.
        low = min(original - limit.total, current)
        high = max(original + limit.total, current)
        capped = _clip(target, low, high)
        if capped != target:
            reasons.append("total")
        target = capped

    ranged = _clip(target, limit.minimum, limit.maximum)
    if ranged != target:
        reasons.append("range")
    target = round(ranged, DECIMALS)

    return ClampedChange(
        name=limit.name,
        key=limit.key,
        requested_change=round(requested_change, DECIMALS),
        applied_change=round(target - current, DECIMALS),
        before=round(current, DECIMALS),
        after=target,
        original=round(original, DECIMALS),
        limited_by=tuple(reasons),
    )
