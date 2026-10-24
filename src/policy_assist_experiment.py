"""Experimental deterministic policy-assessment tool for isolated LLM benchmarking.

Unlike the original no-policy-tool baseline, this mode asks the model to *use*
a computed policy decision from the already implemented Python evaluator.
It is not a measure of the model's independent temporal arithmetic skill.

No database access, no expected benchmark labels, no external API calls.
The trusted history is supplied by the isolated benchmark adapter.
"""
from __future__ import annotations

import copy
from collections.abc import Callable

from src.policy_decisions import evaluate_policy_decision

ASSIST_TOOL_SCHEMA = {
    "name": "evaluate_policy_signals",
    "description": (
        "Evaluate the account's POL-002 warning status and qualifying decline-event "
        "count using the trusted deterministic policy engine. Call this AFTER "
        "get_history to obtain structured policy evidence. Read the returned "
        "warning_signal_status and consecutive_decline_event_count literally; "
        "don't count score transitions yourself. This does not replace "
        "retrieve_policy or prove administrative warning registration."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "account_id": {"type": "integer"},
            "as_of_period": {"type": "string", "description": "Report month (YYYY-MM-DD)"},
        },
        "required": ["account_id", "as_of_period"],
    },
}

ASSIST_PROMPT_APPENDIX = """
CONTROLLED POLICY-TOOL EXPERIMENT (this run only):
- After calling get_history for the requested account, call the available
  evaluate_policy_signals tool with the caller-requested account_id and
  as_of_period. Also call retrieve_policy for the relevant POL-002 passage.
- For policy_claims fields warning_signal_status and
  raw_event_count.consecutive_declines, use the tool's returned computed
  values, not a separate mental count of score decreases or a guess about
  whether the event must end in the reporting month.
- This tool reports a POLICY signal only, never an operational warning state.
- If the tool fails or its evidence is inconsistent with history or policy,
  do not invent claims; report a genuine limitation for safe blocking.
- Python will still independently verify EVERY final score and policy claim.
"""


def build_assist_dispatch(
    history: list[dict], *, account_id: int, as_of_period: str,
) -> Callable[..., dict]:
    """Create a fixture-bound policy tool; refuse other accounts or periods."""
    if type(account_id) is not int or type(as_of_period) is not str or not history:
        raise ValueError("Invalid trusted benchmark context")
    if any(type(row) is not dict for row in history):
        raise ValueError("History rows must be mappings")
    fixed_history = copy.deepcopy(history)

    def evaluate_policy_signals(*, account_id: int, as_of_period: str) -> dict:
        if type(account_id) is not int or account_id != bound_account or type(as_of_period) is not str or as_of_period != bound_period:
            raise ValueError("Benchmark context mismatch")
        # Never use the live database. The evaluator is pure and reads only the
        # closed-over history supplied by the benchmark fixture.
        decision = evaluate_policy_decision(copy.deepcopy(fixed_history), bound_period)
        events = [event for event in decision["events"]
                  if event["criterion"] == "consecutive_declines"]
        return {
            "account_id": bound_account,
            "as_of_period": bound_period,
            "market": decision["market"],
            "window_start": decision["window_start"],
            "window_end": decision["window_end"],
            "warning_signal_status": decision["warning_signal_status"],
            "consecutive_decline_event_count": len(events),
            "consecutive_decline_events": [
                {"period": e["period"], "review_state": e["review_state"]}
                for e in events
            ],
            "coverage_complete": decision.get("coverage_complete"),
        }

    bound_account = account_id
    bound_period = as_of_period
    return evaluate_policy_signals
