"""Donut charts for the leadership overview, drawn as plain SVG (no script).

A donut is used only for a part-to-whole with a few clearly different parts. Every chart carries its
total in the middle and a legend with the exact value and share of each slice, so nothing has to be
judged by eye. Colours are a fixed, colour-blind-checked order (blue, orange, aqua, yellow, magenta);
a colour belongs to the thing it stands for, not to its rank, so it doesn't change as numbers move.
"""

import math
from dataclasses import dataclass, field
from decimal import Decimal

SLOTS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4")
RADIUS = 54
CIRCUMFERENCE = 2 * math.pi * RADIUS
GAP = 2.0  # surface-coloured gap between slices, in px of arc


@dataclass
class Slice:
    label: str
    display: str  # the value as shown: "8" or "$28,880"
    pct: int
    color: str
    dash: str = ""  # stroke-dasharray
    offset: float = 0.0  # stroke-dashoffset


@dataclass
class Donut:
    title: str
    hint: str
    center: str
    center_label: str
    slices: list[Slice] = field(default_factory=list)  # in legend order, zero-valued ones included

    @property
    def drawn(self) -> list[Slice]:
        return [s for s in self.slices if s.dash]


def donut(
    title: str,
    hint: str,
    items: list[tuple[str, Decimal | int, str]],
    center: str,
    center_label: str,
) -> Donut:
    """`items` are (label, value, shown value) in a fixed order; the order decides the colour."""
    if len(items) > len(SLOTS):  # never invent a sixth colour: fold the tail into "Other"
        head, tail = items[: len(SLOTS) - 1], items[len(SLOTS) - 1 :]
        items = [*head, ("Other", sum((Decimal(v) for _, v, _ in tail), Decimal(0)), f"{len(tail)} more")]
    total = sum((Decimal(v) for _, v, _ in items), Decimal(0))
    chart = Donut(title, hint, center, center_label)
    nonzero = sum(1 for _, v, _ in items if Decimal(v) > 0)
    start = 0.0
    for i, (label, value, display) in enumerate(items):
        share = float(Decimal(value) / total) if total else 0.0
        s = Slice(label, display, round(share * 100), SLOTS[i])
        if share > 0:
            length = share * CIRCUMFERENCE
            visible = length if nonzero == 1 else max(length - GAP, 0.75)
            s.dash = f"{visible:.2f} {CIRCUMFERENCE - visible:.2f}"
            s.offset = -start
            start += length
        chart.slices.append(s)
    return chart
