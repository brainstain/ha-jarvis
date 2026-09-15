"""Response-time recording for the e2e suite.

Every test times its own calls through `PerfRecorder.timed` / `.atimed` and
optionally asserts a budget in the same call. Samples accumulate in one
process-wide recorder; at the end of the run `conftest.py` writes them out
as a JSON report (min/mean/p50/p95/max per operation) and prints a summary
table. This is deliberately not a load-testing tool: each test drives one
request at a time, sequentially, and a handful of repeats at most — enough
to get a meaningful spread without pretending to model concurrent traffic
this system will never see.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


@dataclass
class Sample:
    name: str
    seconds: float


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


@dataclass
class PerfRecorder:
    samples: list[Sample] = field(default_factory=list)

    def record(self, name: str, seconds: float) -> None:
        self.samples.append(Sample(name, seconds))

    def timed(
        self,
        name: str,
        fn: Callable[[], T],
        budget: float | None = None,
    ) -> tuple[T, float]:
        """Time a sync call, record it, and (if `budget` is given) assert on it."""
        t0 = time.perf_counter()
        result = fn()
        elapsed = time.perf_counter() - t0
        self.record(name, elapsed)
        if budget is not None:
            assert elapsed <= budget, f"{name} took {elapsed:.2f}s (budget {budget:.2f}s)"
        return result, elapsed

    async def atimed(
        self,
        name: str,
        fn: Callable[[], Awaitable[T]],
        budget: float | None = None,
    ) -> tuple[T, float]:
        """Time an async call, record it, and (if `budget` is given) assert on it."""
        t0 = time.perf_counter()
        result = await fn()
        elapsed = time.perf_counter() - t0
        self.record(name, elapsed)
        if budget is not None:
            assert elapsed <= budget, f"{name} took {elapsed:.2f}s (budget {budget:.2f}s)"
        return result, elapsed

    def stats(self) -> dict[str, dict[str, float]]:
        by_name: dict[str, list[float]] = {}
        for s in self.samples:
            by_name.setdefault(s.name, []).append(s.seconds)

        out: dict[str, dict[str, float]] = {}
        for name, values in by_name.items():
            values_sorted = sorted(values)
            out[name] = {
                "count": len(values),
                "min": min(values),
                "mean": statistics.fmean(values),
                "p50": _percentile(values_sorted, 0.50),
                "p95": _percentile(values_sorted, 0.95),
                "max": max(values),
            }
        return out


# One recorder for the whole test session — see conftest.py's `perf` fixture
# and `pytest_sessionfinish` hook.
PERF = PerfRecorder()
