"""Working-day arithmetic. Weekends only for now; a holiday calendar comes later (techstack §7)."""

from datetime import date, timedelta


def is_working_day(d: date) -> bool:
    return d.weekday() < 5


def add_working_days(d: date, n: int) -> date:
    step = 1 if n >= 0 else -1
    left = abs(n)
    while left:
        d += timedelta(days=step)
        if is_working_day(d):
            left -= 1
    return d
