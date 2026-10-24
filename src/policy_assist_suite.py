"""Module 6.4.5 — all-six-case, opt-in policy-tool-assist benchmark.

A read-only deterministic evaluator bound to the current synthetic fixture.
Unlike the original Module 6.4.4 CF-001 pilot, this supports both standard
and grace markets using *caller-provided* site metadata, never inferred data.
No benchmark labels, live database queries, or original tool dispatch access.
"""
from __future__ import annotations

import copy
from collections.abc import Callable

from src.policy_decisions import evaluate_policy_decision


# Same model-facing tool contract as the original CF-001 pilot: the differences
# are the trusted context bound *inside* the Python dispatcher.
SUITE_TOOL_SCHEMA = {
    "name": "evaluate_policy_signals",
    "description": (
        "Read-only policy evaluation using the recorded account history and trusted "
        "site metadata (if applicable). Returns computed warning status, count of "
        "qualifying consecutive-decline EVENTS, and POL-004 grace mode, threshold "
        "and review state. Call after get_history. Do not substitute independent "
        "counting, infer onboarding dates, or treat the signal as official action."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "account_id": {"type": "integer"},
            "as_of_period": {"type": "string", "description": "Report month YYYY-MM-DD"},
        },
        "required": ["account_id", "as_of_period"],
    },
}

# Scope-specific rules refer to policy FIELD TYPES, not hardcoded outcomes.
# Crucially, CF-004 must report a truthful insufficient_evidence status and
# zero count, and allow the existing composer to block with gather_evidence.
SUITE_PROMPT_APPENDICES = {
    "score_bin_warning_signal_decline_events": """
CONTROLLED SIX-CASE POLICY TOOL ASSIST (current fixture only):
- After get_history, call evaluate_policy_signals for the requested account and
  reporting month; also retrieve applicable POL-002 policy.
- For warning_signal_status and raw_event_count.consecutive_declines, use the
  tool's warning_signal_status and consecutive_decline_event_count literally.
  Count qualifying STREAK-ENDING EVENTS, not individual downward transitions.
- Provide two actual score_claims for the reporting month, policy_claims for
  precisely these two fields, and no unsupported additional claims.
- Routine warnings about administrative registration/clearance are rendered
  by trusted Python. If all mandatory evidence is established, limitations [].
  If truly missing required evidence, describe the gap and do not fabricate.
""",
    "score_bin_warning_signal_no_declines": """
CONTROLLED SIX-CASE POLICY TOOL ASSIST (current fixture only):
- Call get_history, retrieve_policy and evaluate_policy_signals.
- Use computed warning_signal_status and consecutive_decline_event_count
  from the tool to populate exactly TWO policy_claims, with fields
  warning_signal_status and raw_event_count.consecutive_declines.
- Include exactly TWO actual score_claims for the reporting month: score and bin.
- Never assert not_triggered merely because the event count is zero; other
  warning criteria or incomplete eight-month coverage can change status.
- Trusted Python writes the public caveats. Set limitations [] only when all
  required report evidence is supported; otherwise report genuine gaps.
""",
    "score_bin_warning_signal_quality_hold": """
CONTROLLED SIX-CASE POLICY TOOL ASSIST (current fixture only):
- Call get_history, retrieve_policy and evaluate_policy_signals.
- Exactly TWO score_claims for the requested score and bin, and TWO policy_claims
  for warning_signal_status and raw_event_count.consecutive_declines.
- Use the policy tool values literally: pending_data_quality means held events,
  not an eligible trigger; insufficient_evidence means missing baseline coverage,
  not not_triggered. This report's trusted composer determines whether to
  PUBLISH a verified hold or BLOCK an insufficient-evidence assessment.
- When insufficient_evidence is itself established by the trusted evaluator,
  explicitly claim insufficient_evidence and the computed count; do NOT conceal
  the gap or claim not_triggered. A limitations list can be [] if there are no
  ADDITIONAL unresolved evidence gaps; the deterministic composer will still
  block publication and request more evidence. If other required evidence is
  missing, report that limitation and do not invent claims.
""",
    "grace_decline_events": """
CONTROLLED SIX-CASE POLICY TOOL ASSIST (current fixture only):
- Call get_history, retrieve_policy (POL-002/POL-004, POL-005 when relevant),
  then evaluate_policy_signals for the caller-requested account/report month.
- The trusted caller supplies site onboarding and last quarterly-review dates.
  NEVER infer these from the score history or invent a review status.
- score_claims must be []; include precisely FOUR policy_claims:
    policy_applicability.mode = tool.policy_applicability.mode
    policy_applicability.thresholds.consecutive_declines =
      tool.policy_applicability.thresholds.consecutive_declines
    policy_applicability.quarterly_exception_review =
      tool.policy_applicability.quarterly_exception_review
    raw_event_count.consecutive_declines = tool.consecutive_decline_event_count
- Use the computed values, never assume a grace threshold or an eligible
  event based solely on the number of downward score transitions.
- Reporting a policy event is NOT official warning registration or credit action.
  Trusted Python handles routine administrative caveats. Report genuine missing
  mandatory evidence in limitations; otherwise limitations may be [].
""",
}


# Module 6.5.3: explicit producer/composer contract for the two scopes whose
# live diagnostic claims passed but rendering was blocked by limitations.
# Prompt only: never remove or reinterpret a model-produced limitation in Python.
_SUITE_SCOPE_LIMITATIONS_CONTRACT = """
STRUCTURED LIMITATIONS CONTRACT FOR THIS REQUESTED SCOPE:
- The limitations JSON array records unresolved REQUIRED EVIDENCE, not general
  disclaimers, policy explanations, or an explanation of what this report does
  not authorize. A nonempty list blocks this scope's trusted composer even when
  the individual submitted claims passed verification.
- For a no-decline report, mandatory evidence must support the requested score,
  bin, warning status and qualifying-event count for the exact caller context.
  A supported no-trigger conclusion does not require proof of administrative
  warning registration or clearance, causes, or events outside the stated window.
- For a grace-market report, mandatory evidence includes the caller-owned account,
  period, market, site onboarding and quarterly review context, applicable policy,
  recorded history, mode, threshold, review state and qualifying-event count.
  Grace mode alone never establishes that an event is eligible or unheld.
- The trusted report already states its scope boundaries: a computed event is
  not an operational warning, clearance, proof of cause, or authorized credit
  action; a verified quality hold is not permission to escalate that event.
  Do not request administrative evidence that is outside the requested scope.
- AFTER checking all mandatory evidence, if no required evidence remains missing
  or unresolved, output exactly "limitations": []. Do not populate this array
  with routine caveats or copy the illustrative nonempty schema placeholder.
- If any required evidence really is missing, stale, conflicting or insufficient,
  retain a specific limitation and omit unsupported claims. Do not hide gaps,
  fabricate metadata, or force [] to obtain publication. Independent Python
  verification and the publication gate remain authoritative.
"""
for _scope in ('score_bin_warning_signal_no_declines', 'grace_decline_events'):
    SUITE_PROMPT_APPENDICES[_scope] += _SUITE_SCOPE_LIMITATIONS_CONTRACT


def build_suite_assist_dispatch(
    history: list[dict], *, account_id: int, as_of_period: str,
    market: int, site_onboarded_period: str | None = None,
    last_exception_review_period: str | None = None,
) -> Callable[..., dict]:
    """Provide exactly one fixture's authoritative policy signals to the model.

    Site metadata is trusted from the *request* and not supplied by the model.
    Only the bound account+period can be evaluated; other contexts raise.
    """
    if (type(account_id) is not int or type(market) is not int
            or type(as_of_period) is not str or type(history) is not list
            or not history or any(type(row) is not dict for row in history)):
        raise ValueError("Invalid trusted suite context")
    if market == 1:
        if site_onboarded_period is not None or last_exception_review_period is not None:
            raise ValueError("Standard market does not take site metadata")
    elif (type(site_onboarded_period) is not str or
          type(last_exception_review_period) is not str):
        raise ValueError("Grace-market caller metadata is required")
    rows = copy.deepcopy(history)
    if any(row.get("market") != market for row in rows):
        raise ValueError("Fixture market differs from caller context")

    # Fail before contacting the LLM if authoritative request context is bad.
    def _decide() -> dict:
        return evaluate_policy_decision(
            copy.deepcopy(rows), as_of_period,
            site_onboarded_period=site_onboarded_period,
            last_exception_review_period=last_exception_review_period,
        )

    _decide()

    def evaluate_policy_signals(*, account_id: int, as_of_period: str) -> dict:
        if (type(account_id) is not int or account_id != bound_account
                or type(as_of_period) is not str or as_of_period != bound_period):
            raise ValueError("Benchmark context mismatch")
        result = _decide()
        applicability = result["policy_applicability"]
        events = [event for event in result["events"]
                  if event["criterion"] == "consecutive_declines"]
        return {
            "account_id": bound_account,
            "as_of_period": bound_period,
            "market": bound_market,
            "window_start": result["window_start"],
            "window_end": result["window_end"],
            "warning_signal_status": result["warning_signal_status"],
            "consecutive_decline_event_count": len(events),
            "consecutive_decline_events": [
                {"period": item["period"], "review_state": item["review_state"]}
                for item in events
            ],
            "coverage_complete": result["coverage_complete"],
            "missing_baseline_months": copy.deepcopy(result["missing_baseline_months"]),
            "policy_applicability": {
                "mode": applicability["mode"],
                "quarterly_exception_review": applicability["quarterly_exception_review"],
                "thresholds": {
                    "consecutive_declines": applicability["thresholds"]["consecutive_declines"],
                },
            },
        }

    bound_account, bound_period, bound_market = account_id, as_of_period, market
    return evaluate_policy_signals
