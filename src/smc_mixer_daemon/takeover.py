"""Per-strip fader ownership, with short-lived acknowledgments for our writes."""

import time
from dataclasses import dataclass, field


def percent(value: int) -> int:
    return round(value * 100 / 127)


@dataclass
class Takeover:
    position: int
    observed: float
    soft: bool
    owned: bool = False
    expected: list[tuple[int, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.target = min(100.0, max(0.0, self.observed))
        self.owned = not self.soft or abs(self.position - self.target) <= 1

    def observe(self, volume: float) -> None:
        now = time.monotonic()
        self.expected = [(value, until) for value, until in self.expected if until >= now]
        changed = abs(volume - self.observed) > 0.1
        self.observed = volume
        if self.soft and changed and not any(abs(volume - value) <= 0.1 for value, _ in self.expected):
            self.target = min(100.0, max(0.0, volume))
            self.owned = False

    def move(self, value: int) -> bool:
        previous, self.position = self.position, value
        if not self.owned:
            self.owned = abs(value - self.target) <= 1 or (previous - self.target) * (value - self.target) <= 0
        return self.owned

    def written(self, value: int) -> None:
        self.expected.append((value, time.monotonic() + 2))
        self.expected = self.expected[-100:]
