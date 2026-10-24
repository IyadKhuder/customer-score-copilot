"""Module 6.4.6: rate-limit-aware pacing for the six-case assisted benchmark.

This is an *evaluation-only* wrapper around existing validated components:
- Module 6.2 preserves the report schema and fixture isolation.
- Module 6.4.5 supplies the opt-in, deterministic policy tool.
- A caller-owned sleep function controls between-case pauses and bounded 429 retries.

Use serially only; src.live_evaluation patches global agent dispatch during each case.
"""
from __future__ import annotations

import re
from time import monotonic, sleep
from typing import Any, Callable

from src.evaluation_runner import run_benchmark
from src.live_evaluation import run_live_case
from src.policy_passage_variants import POLICY_PASSAGE_VARIANTS

_PROVIDER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


def is_rate_limit_error(exc: Exception) -> bool:
    """Recognize provider 429 errors without reading/logging exception messages."""
    if type(exc).__name__ in {"RateLimitError", "TooManyRequestsError"}:
        return True
    return getattr(exc, "status_code", None) == 429


def run_paced_assisted_benchmark(
    client: Any, *, provider_id: str,
    policy_passage_variant: str = "clarified_v1",
    max_tool_rounds: int = 4,
    between_case_seconds: float = 65.0,
    retry_delay_seconds: float = 65.0,
    max_rate_limit_retries: int = 1,
    benchmark: dict | None = None,
    fixtures: dict | None = None,
    sleeper: Callable[[float], None] = sleep,
    clock: Callable[[], float] = monotonic,
) -> dict:
    """Run six fixed cases, pausing between cases and retrying bounded 429s.

    A retry restarts the *whole case*, because a 429 can happen after several
    provider/tool turns. Earlier provider calls may already have consumed quota.
    All other exceptions pass through for Module 6.2 to record by type.

    The unchanged Module 6.2 report contains only safe observations. Per-case
    elapsed_ms excludes the deliberate *between-case* cooldown, but includes
    per-case provider work and any rate-limit backoff/retry time.
    """
    if not (callable(getattr(client, "chat", None)) and
            callable(getattr(client, "continue_with_tool_results", None))):
        raise TypeError("A live provider adapter is required")
    if type(provider_id) is not str or not _PROVIDER_ID.fullmatch(provider_id):
        raise ValueError("Invalid provider identifier")
    if policy_passage_variant not in POLICY_PASSAGE_VARIANTS:
        raise ValueError("Unsupported policy passage variant")
    if type(max_tool_rounds) is not int or not 1 <= max_tool_rounds <= 8:
        raise ValueError("max_tool_rounds must be between 1 and 8")
    if (type(between_case_seconds) not in (float, int) or
            not 0 <= between_case_seconds <= 600):
        raise ValueError("between_case_seconds must be between 0 and 600")
    if (type(retry_delay_seconds) not in (float, int) or
            not 0 <= retry_delay_seconds <= 600):
        raise ValueError("retry_delay_seconds must be between 0 and 600")
    if type(max_rate_limit_retries) is not int or not 0 <= max_rate_limit_retries <= 3:
        raise ValueError("max_rate_limit_retries must be between 0 and 3")
    if not callable(sleeper) or not callable(clock):
        raise TypeError("A callable sleep and monotonic clock are required")

    # No validation/provider access is hidden in this callback: run_benchmark
    # validates the exact frozen benchmark and fixtures BEFORE invoking it.
    attempts_by_case: dict[str, int] = {}
    timed_ms: dict[str, float] = {}
    invocation_count = 0

    def executor(case: dict, fixture: dict) -> dict:
        nonlocal invocation_count
        # Record an error-safe duration even if a supplied cooldown hook fails
        # before this case actually starts. The generic runner then records
        # that failure by type instead of crashing during report finalization.
        timed_ms[case["id"]] = 0.0
        if invocation_count:
            sleeper(float(between_case_seconds))
        invocation_count += 1
        started = clock()
        retries = 0
        try:
            while True:
                try:
                    return run_live_case(
                        case, fixture, client,
                        max_tool_rounds=max_tool_rounds,
                        policy_passage_variant=policy_passage_variant,
                        policy_tool_suite=True,
                    )
                except Exception as exc:
                    if not is_rate_limit_error(exc) or retries >= max_rate_limit_retries:
                        raise
                    retries += 1
                    # Exponential backoff, bounded by configured retry count.
                    sleeper(float(retry_delay_seconds) * (2 ** (retries - 1)))
        finally:
            timed_ms[case["id"]] = round(max(0.0, clock() - started) * 1000, 3)
            attempts_by_case[case["id"]] = retries

    report = run_benchmark(
        executor, benchmark=benchmark, fixtures=fixtures,
        provider_id=provider_id, execution_mode="provider_adapter",
    )
    # The generic runner times the executor, including the optional cooldown
    # at the beginning of the following case. Restore true per-case durations
    # while keeping its strict, scoreable schema unchanged.
    for row in report["cases"]:
        row["elapsed_ms"] = timed_ms[row["case_id"]]
    return report
