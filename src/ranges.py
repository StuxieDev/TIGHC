"""Intensity/duration bands - the [low, high] ranges every binding rolls a random value from, so nothing feels perfectly repetitive."""

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class FloatRange:
    """A min/max band with a low <= high invariant. Base for both intensity and duration bands."""

    low: float
    high: float

    def __post_init__(self):
        """Validate low <= high - catches typos in the config (e.g. swapped low/high) at startup instead of failing obscurely mid-game."""
        if self.low > self.high:
            raise ValueError(f"Invalid range ({self.low}, {self.high}): low must be <= high")

    def roll(self) -> float:
        """Pick a uniformly-random value between low and high (inclusive)."""
        return random.uniform(self.low, self.high)


@dataclass(frozen=True)
class VibeRange(FloatRange):
    """A 0.0-1.0 intensity band. Each trigger rolls a random value inside it."""

    def __post_init__(self):
        """Extend FloatRange's low<=high check with the 0.0-1.0 intensity bound."""
        super().__post_init__()
        if not (0.0 <= self.low and self.high <= 1.0):
            raise ValueError(f"Invalid intensity range ({self.low}, {self.high}); must be within 0.0-1.0")

    def __str__(self) -> str:
        """Percent display for banners/GUI, e.g. "40-65%"."""
        return f"{self.low * 100:.0f}-{self.high * 100:.0f}%"


@dataclass(frozen=True)
class DurationRange(FloatRange):
    """A band of seconds. Each pulse rolls a random duration inside it, so pulses don't all feel identical."""

    def __str__(self) -> str:
        """Seconds display for banners/GUI, e.g. "0.30-0.40s"."""
        return f"{self.low:.2f}-{self.high:.2f}s"


@dataclass(frozen=True)
class PulseSpec:
    """A one-shot binding's intensity band plus how long that pulse should last."""

    vibe: VibeRange
    duration: DurationRange

    def roll_duration(self) -> float:
        """Convenience shortcut for `self.duration.roll()` - how long this pulse's next firing should last."""
        return self.duration.roll()


if __name__ == "__main__":
    print(f"{__file__} is TIGHC's intensity/duration-range module - it's a library, not meant to be run directly.")
    print("Run `python cli.py` (from the repo root) for the headless CLI, or `python gui.py` for the interactive GUI.")
