"""Behavioral Score Trajectory Copilot — Module 5.5.6.4 temporal claim-first integration.

The LLM provider adapter is unchanged: .chat(...) followed by
.continue_with_tool_results(...) during tool use. Once it stops calling
Python tools, the FINAL text must be structured JSON. The only public
answer returned by this module comes from the response gate.
"""

from src.score_tools import TOOL_DISPATCH, TOOL_SCHEMAS
from src.retriever import RETRIEVER_TOOL_SCHEMA, retrieve_policy
from src.verification_pipeline import verify_structured_response
from src.response_gate import handle_verification
from src.narrative_composer import (
    CompositionError, compose_verified_score_bin, compose_verified_warning_signal,
    compose_verified_score_bin_warning_signal,
)
from src.temporal_narrative import (
    TemporalNarrativeError, compose_verified_decline_event_narrative,
)
from src.combined_temporal_narrative import (
    CombinedTemporalNarrativeError, compose_verified_full_narrative,
)
from src.zero_decline_narrative import (
    ZeroDeclineNarrativeError, compose_verified_no_decline_narrative,
)
from src.quality_hold_narrative import (
    QualityHoldNarrativeError, InsufficientPolicyEvidenceError,
    compose_verified_quality_hold_narrative,
)
from src.grace_decline_narrative import (
    GraceDeclineNarrativeError, compose_verified_grace_declines,
)


SYSTEM_PROMPT = """You are a behavioral credit-score analysis copilot.

Use score query tools to obtain account records and numeric facts; never invent
scores, dates, changes, or account details. Use retrieve_policy to obtain policy
rules and recommendations before interpreting risk or suggesting an action.
Do not guess policy thresholds from memory. Distinguish observed score movements
from their possible causes: score history alone does not prove why behavior changed.
If evidence is missing, say so. Note possible scoring/data-quality artifacts and
cite policy memo names where the retrieved passages support your conclusions.

Before giving a risk recommendation, evaluate the applicable
policy criteria against the available score-tool evidence.
Retrieve the relevant policy passages when needed.

Explicitly distinguish:
- Historical triggers that occurred during the account's history.
- Triggers applicable to the latest reporting period.
- Triggers within the policy's rolling look-back window.

For each relevant criterion, state whether it is met,
not met, or cannot be determined.

Do not defer an evaluation to manual review if the
available tool results and policy rules are sufficient
to perform it. If evidence is insufficient or a policy
rule is ambiguous, explain precisely what is missing
or ambiguous.

When evaluating time-dependent policy triggers, distinguish
between a historical trigger occurrence, a trigger that is
still ongoing in the latest reporting period, and the
current status of a previously triggered warning.

A score recovery may end an ongoing decline streak, but
it does not automatically erase an earlier warning event.

When using rolling look-back windows, examine the dates
of qualifying events and explain the window interpretation.

Do not assume that a logged warning automatically expires
unless the retrieved policy explicitly specifies a
clearance or expiration rule.

If the policy does not define how or when an existing
warning is cleared, identify that ambiguity rather than
asserting that the account has no active warning.
"""

# Prompt-level JSON formatting is provider-neutral. It is NOT a guarantee:
# the schema parser + evidence checks + publication gate enforce acceptance.
STRUCTURED_OUTPUT_INSTRUCTIONS = """
Your FINAL reply (after any tool calls) must be ONE JSON object only.
No Markdown fences, comments, or text before/after the JSON. Include exactly:
{
  "answer": "analyst-facing narrative",
  "score_claims": [
    {"account_id": 81000001, "period": "1991-12-01",
     "metric": "score", "value": 271, "market": 1}
  ],
  "policy_claims": [
    {"field": "warning_signal_status", "value": "triggered",
     "account_id": 81000001, "as_of_period": "1991-12-01", "market": 1}
  ],
  "limitations": ["evidence limitations, if any"]
}
The JSON shown above describes FIELD SHAPES ONLY, not facts to copy.
Use only evidence obtained for the current question.
- Before stating account-specific score facts or policy conclusions, call
  get_history for that account. A short summary from compute_trend alone is
  not enough for current claim verification.
- Every explicitly reported observation of score or bin must appear in
  score_claims with its account, reporting date, metric, value, and market.
- Put checkable policy/temporal conclusions in policy_claims with the
  correct field, value, account, reporting period, and market. Do not invent
  fields the policy decision system cannot verify.
- Include uncertainty or unavailable records under limitations. Do not
  disguise unverified assumptions as factual conclusions.
- Omit claims you cannot support; never fabricate them to satisfy the schema.
- If data is insufficient for a responsible answer, say exactly what is
  missing rather than making up values or policy rules.
"""

CLAIM_FIRST_INSTRUCTIONS = """
This run is a NUMERIC-ONLY score-and-bin report. The public explanation will
be generated by trusted Python code, NOT from your answer prose.
- Call get_history for the requested account.
- Include precisely two score_claims for the requested date: one metric "score"
  and one metric "bin", using the same verified market.
- Do not invent a value to satisfy this requirement; list limitations if the
  evidence is missing (the program will withhold publication).
- Do not include policy conclusions: policy_claims should be [] for this scope.
- The "answer" field is still required by the JSON schema, but is an
  UNPUBLISHED draft. It will never be copied into the public response.
"""

CLAIM_FIRST_POLICY_INSTRUCTIONS = """
This run is a POLICY-ONLY early-warning signal report for the requested account
and reporting period. The public explanation will be generated by trusted Python
code, NOT from your answer prose.
- Call get_history for the requested account, and retrieve_policy for relevant
  early-warning policy passages.
- Include precisely one policy_claim with field "warning_signal_status" and
  value "triggered" ONLY IF the evidence and rules support that conclusion.
- The claim must use the requested account, reporting period, and market.
- Do not include numerical claims: score_claims should be [] for this scope.
- Never invent a signal just to satisfy the format. If the signal cannot be
  verified, omit the claim and describe the gap in limitations; publication
  will be withheld.
- The "answer" field is an UNPUBLISHED draft. Its text is never public.
"""

CLAIM_FIRST_COMBINED_INSTRUCTIONS = """
This run is a COMBINED score, bin, and early-warning signal report for one
requested account, market, and reporting period. Trusted Python code will
compose the public explanation; your answer prose will never be published.
- Call get_history for the requested account and retrieve_policy for the
  applicable early-warning rules.
- Include exactly two score_claims for the requested reporting date: one
  metric "score" and one "bin", both from the same verified market.
- Include exactly one policy_claim with field "warning_signal_status" and
  value "triggered" ONLY when the policy and score history support it.
- All claims must refer to the caller-requested account, date, and market.
- Do not invent scores or policy findings to fill missing fields; if a claim
  cannot be substantiated, leave it out and state the gap in limitations.
  The response gate will withhold publication for incomplete responses.
- No additional claims or policy fields are supported in this scope.
- Do not claim an operational warning was recorded, explain an unverified
  cause, or assert that any historical warning was cleared.
- The JSON "answer" is an UNPUBLISHED draft, never public-facing text.
"""


CLAIM_FIRST_DECLINE_EVENTS_INSTRUCTIONS = """
This run is a TEMPORAL-EVENT-ONLY report for the caller-requested account,
market 1, and reporting period. Trusted Python code renders the public narrative;
your JSON answer draft will NEVER be published.
- Call get_history for the requested account and retrieve_policy for POL-002
  (three consecutive score declines) and, where relevant, POL-005.
- Include score_claims: [] because this renderer reports policy-evaluated temporal
  events, not a separately verified score/bin snapshot.
- Include exactly one policy_claim with field
  "raw_event_count.consecutive_declines" and a nonnegative integer count
  ONLY when the recorded history and policy evaluation support that count.
  For publication this count must be positive; no-events cases are withheld.
- Use the requested account, as_of_period, and market in that policy_claim.
- Include no extra policy claims: this renderer cannot silently omit them.
- Never claim a warning was registered, cleared, or escalated, nor that the
  observed pattern continues beyond its event date.
- If evidence is missing, do not guess; omit the claim and state limitations.
- The JSON "answer" is an UNPUBLISHED draft and has no publication authority.
"""


CLAIM_FIRST_FULL_NARRATIVE_INSTRUCTIONS = """
This is a FULL COMBINED score, bin, warning-signal and TEMPORAL-DECLINE-EVENT
report for ONE caller-requested account in MARKET 1 and one reporting month.
Only trusted Python code renders the public narrative. The JSON answer is an
UNPUBLISHED draft, never a source of facts.
- Call get_history for the account and retrieve_policy for POL-002 (and POL-005
  when data quality matters).
- Include exactly TWO score_claims: score and bin for the requested date/market.
- Include exactly TWO policy_claims for the requested date/market:
  1. field "warning_signal_status", value "triggered" only if justified.
  2. field "raw_event_count.consecutive_declines", value the justified,
     nonnegative integer event count in the policy evaluation window.
- Do not invent a status or count to satisfy the format. If claims are missing
  or the event count is zero, this narrow report will not be published.
- Do not add any other claims. Do not assert official warning registration,
  causal explanations for score changes, clearance, or a risk action.
- If evidence is insufficient, omit unsupported claims and state limitations;
  the response gate will withhold publication rather than fabricating facts.
"""

CLAIM_FIRST_NO_DECLINES_INSTRUCTIONS = """
This is a MARKET-1 score/bin and verified NO-DECLINE report for ONE account
and ONE reporting period. The program, not your prose, creates the public answer.
- Call get_history and retrieve the applicable POL-002 policy passage.
- Provide exactly TWO score_claims: score and bin on the requested date.
- Provide exactly TWO policy_claims: "warning_signal_status" and
  "raw_event_count.consecutive_declines" for the same date and market.
- Only report "not_triggered" and count 0 if those conclusions are truly
  supported by the retrieved history and policy. Never guess to fit this scope.
- If a warning was triggered, events occurred, or evidence is incomplete,
  report the truthful claim and limitations; the narrow composer will block.
- The JSON "answer" is an UNPUBLISHED draft, never public evidence.
"""

CLAIM_FIRST_QUALITY_HOLD_INSTRUCTIONS = """
This is a MARKET-1 score/bin and DATA-QUALITY STATUS report for one account
and one reporting month. Only trusted Python code may create public prose.
- Call get_history, retrieve_policy for POL-002 and POL-005.
- Include exactly TWO score_claims (score and bin) and exactly TWO policy_claims:
  "warning_signal_status" and "raw_event_count.consecutive_declines".
- Report the independently supported policy value, never guess:
  "pending_data_quality" when the data-quality hold applies, or
  "insufficient_evidence" when the history cannot support a conclusive status.
- Do not turn insufficient evidence into "not_triggered"; the program blocks
  publication of an insufficient-evidence case and requests more evidence.
- No additional claims, inferred clearance, referrals or risk actions.
- The JSON answer is an UNPUBLISHED draft, never public-facing evidence.
"""

# Scope-specific guidance; the verified quality-hold renderer remains fail-closed.
# The routine caveat "status pending review" must not be mistaken for a gap
# in proving that the review state itself is pending. Real gaps still block.
CLAIM_FIRST_QUALITY_HOLD_LIMITATIONS_INSTRUCTIONS = """
LIMITATION SEMANTICS FOR THE DATA-QUALITY STATUS REPORT:
- The public narrative is composed by trusted Python; your JSON "answer" is
  UNPUBLISHED and your "limitations" list is NOT pasted into public prose.
- Distinguish the VERIFIED STATE of a data-quality hold from an unverified
  final policy or administrative decision. If get_history, the retrieved
  POL-002/POL-005 passages, and available policy-tool results genuinely
  support the account's score, bin, "pending_data_quality" status, and the
  raw qualifying-event count for the requested context, set "limitations": []
  exactly. A review still being pending is the SUBJECT of this report, not
  missing evidence for the fact that the hold exists. The trusted renderer
  already explains that the event is under review and does not establish an
  operational warning, automatic clearance, or an authorized credit action.
- Do not fill "limitations" with generic caveats about pending review,
  uncertain causes, unavailable administrative registers, or the illustrative
  JSON schema placeholder if those are outside the FOUR required claims.
- A result of "insufficient_evidence" is DIFFERENT: when historical coverage
  is inadequate, truthfully report the verified policy status (and event count
  only if verified) and state the concrete missing history in "limitations".
  The application MUST BLOCK publication and request more evidence; NEVER
  describe that status as "not_triggered" or as a cleared warning.
- If evidence required for ANY of the four claims is missing, contradictory,
  unverifiable or mismatched to the account, market, event dates or reporting
  period, OMIT unsupported claims and describe the actual gap in "limitations".
  Do NOT set an empty list to force a PUBLISH decision, infer event counts, or
  copy policy values from an illustrative prompt example.
- Do NOT claim that an administrative warning was registered or cleared,
  that score movements have a verified cause, or that risk action is approved.
- These are instructions, not a bypass: independent verification and the
  existing fail-closed narrative composer remain the publication authority.
"""


CLAIM_FIRST_GRACE_DECLINES_INSTRUCTIONS = """
This is a POL-004 GRACE-MARKET DECLINE-EVENT report, with trusted caller-provided
site-onboarding and exception-review dates. Only Python renders the public prose.
- Call get_history for the requested account and retrieve_policy for POL-002 and POL-004.
  Retrieve POL-005 when a quality hold might affect event eligibility.
- score_claims must be [] (event-only report).
- Include exactly FOUR independently supportable policy_claims:
  "policy_applicability.mode", "policy_applicability.thresholds.consecutive_declines",
  "policy_applicability.quarterly_exception_review", and
  "raw_event_count.consecutive_declines".
- Never invent "grace", "current", a threshold, or events merely to fill fields.
  This scope publishes only when the reviewed grace mode uses four transitions
  and at least one qualifying dated event matches the recorded history.
- Reporting a policy event is NOT claiming a registered warning or authorized risk action.
- The JSON answer is an UNPUBLISHED draft; never rely on its prose.
"""

# Module 6.4 diagnostic refinement: define the event-count claim precisely.
# This is a schema/measurement instruction, not an expected answer for a case.
CONSECUTIVE_DECLINE_EVENT_COUNT_INSTRUCTIONS = """
For the policy claim field "raw_event_count.consecutive_declines":
- The value counts QUALIFYING STREAK-ENDING EVENTS inside the policy's rolling
  three-calendar-month evaluation window. It does NOT count individual monthly
  downward transitions (score decreases).
- For a standard market, an event needs THREE consecutive downward transitions
  across FOUR consecutive monthly observations. For a reviewed POL-004 grace
  market, use the applicable FOUR-transition threshold across FIVE observations.
- Examine EACH potential event-ending month inside the three-month window.
  Count 1 for each month whose immediately preceding threshold-length sequence
  is strictly decreasing with no missing months. An earlier baseline month can
  support an event ending inside the window.
- Example of the counting unit (NOT benchmark history): a history of scores
  650 -> 640 -> 630 -> 620 has THREE downward transitions, but represents ONE
  qualifying three-transition event ending at 620. A longer uninterrupted
  streak may produce multiple overlapping events with different ending months.
- Do not equate 'warning_signal_status=triggered' with any particular event
  count: other policy criteria may trigger the warning. Use the actual history;
  report 0 only when no qualifying events end inside the evaluation window.
- Never copy the illustrative numbers into a claim or infer expected case labels.
"""


# Module 6.4.2: LLM-to-composer contract for the FULL combined temporal scope.
# This is prompt guidance only. Validation and the strict limitations gate stay
# authoritative; no LLM-supplied limitation is silently discarded.
CLAIM_FIRST_FULL_LIMITATIONS_INSTRUCTIONS = """
LIMITATION SEMANTICS FOR THIS FULL COMBINED REPORT:
- The public report is rendered by trusted Python, not from your JSON answer.
- This renderer ALREADY provides standard caveats that score changes do not
  establish their cause, historical declines need not be ongoing, and a
  policy-evaluated warning signal does not establish that an operational warning
  was registered, cleared, or that a risk action was authorized. These are
  BOUNDARIES OF THIS REPORT, not missing evidence for the four required claims.
- If, after checking get_history and retrieved policy evidence, all FOUR
  required claims (score, bin, warning_signal_status, and
  raw_event_count.consecutive_declines) are genuinely supported for the
  caller-requested account, period and market, set "limitations": [] exactly.
  Do not fill limitations with routine scope caveats already stated by the
  trusted renderer; do not copy the nonempty illustrative JSON placeholder.
- If evidence needed for ANY required claim is absent, inconsistent,
  contradictory or cannot be established from the retrieved history/policy,
  OMIT the unsupported claim and include a concrete description of the gap in
  "limitations". The program MUST block publication in that case. Never
  fabricate a value, claim a missing document was retrieved, or force an empty
  list merely to obtain publication.
- Keep administrative-register facts, causes, and proposed credit actions OUT
  of your claims. Do not assert those facts in the unpublished answer either.
- This instruction is NOT permission to ignore a real verification gap. The
  independent schema, numeric, policy, and temporal checks remain mandatory.
"""

_COUNTED_EVENT_SCOPES = frozenset({
    "decline_events",
    "score_bin_warning_signal_decline_events",
    "score_bin_warning_signal_no_declines",
    "score_bin_warning_signal_quality_hold",
    "grace_decline_events",
})

ALL_TOOLS = [*TOOL_SCHEMAS, RETRIEVER_TOOL_SCHEMA]
ALL_TOOL_DISPATCH = {**TOOL_DISPATCH, "retrieve_policy": retrieve_policy}

_UNVERIFIED_FALLBACK = (
    "I could not produce a verified analysis from the available evidence. "
    "Please review the diagnostic status before using this result."
)


def _result(decision: dict, evidence: list[dict], rounds: int,
            *, completed: bool, audit: dict | None = None) -> dict:
    """Use only response-gate authorized prose as the outward-facing answer."""
    return {
        "answer": decision["public_answer"],
        "completed": completed,  # LLM/tool loop ended, NOT proof of validity.
        "status": decision["status"],
        "publishable": decision["publishable"],
        "next_action": decision["next_action"],
        "reason_codes": decision["reason_codes"],
        "tool_evidence": evidence,
        "tool_rounds": rounds,
        # For internal diagnostics only; audit may include an unverified draft.
        "verification": audit,
    }


def _blocked(code: str, evidence: list[dict], rounds: int,
             *, completed: bool, next_action: str = "manual_review",
             audit: dict | None = None) -> dict:
    decision = {
        "public_answer": _UNVERIFIED_FALLBACK,
        "status": "incomplete", "publishable": False,
        "next_action": next_action, "reason_codes": [code],
    }
    return _result(decision, evidence, rounds, completed=completed, audit=audit)


def run_agent(question: str, llm, max_tool_rounds: int = 8,
              verbose: bool = True, *, publication_mode: str = "legacy",
              expected_account_id: int | None = None,
              expected_period: str | None = None,
              claim_scope: str = "score_bin",
              expected_market: int | None = None,
              site_onboarded_period: str | None = None,
              last_exception_review_period: str | None = None,
              tool_schemas: list[dict] | None = None,
              tool_dispatch: dict | None = None) -> dict:
    """Run the agent, validate its structured reply, then apply the gate.

    The adapter's interface stays the same. There are NO automatic LLM retries:
    the returned next_action is a recommendation for a later orchestration step.

    Callers may display only result['answer'] when displaying an analyst reply.
    Never display result['verification']['answer'] without separate approval.
    """
    # Caller-local capabilities: never mutate the application-global tool registry.
    if (tool_schemas is None) != (tool_dispatch is None):
        raise ValueError("Supply tool_schemas and tool_dispatch together")
    if tool_schemas is not None:
        from copy import deepcopy
        if (type(tool_schemas) is not list or type(tool_dispatch) is not dict
                or any(type(t) is not dict or type(t.get("name")) is not str for t in tool_schemas)):
            raise ValueError("Invalid local tool registry")
        names = [t["name"] for t in tool_schemas]
        if (len(names) != len(set(names)) or set(names) != set(tool_dispatch)
                or any(not callable(f) for f in tool_dispatch.values())):
            raise ValueError("Local schemas and dispatch must match")
        active_tools, active_dispatch = deepcopy(tool_schemas), dict(tool_dispatch)
    else:
        active_tools, active_dispatch = ALL_TOOLS, ALL_TOOL_DISPATCH

    if not isinstance(question, str) or not question.strip():
        raise ValueError("The question cannot be empty.")
    if max_tool_rounds < 1:
        raise ValueError("max_tool_rounds must be at least 1.")

    if publication_mode not in ("legacy", "claims_first"):
        raise ValueError("Unknown publication_mode")
    if claim_scope not in ("score_bin", "warning_signal", "score_bin_warning_signal", "decline_events", "score_bin_warning_signal_decline_events", "score_bin_warning_signal_no_declines", "score_bin_warning_signal_quality_hold", "grace_decline_events"):
        raise ValueError("Unknown claim_scope")
    if publication_mode == "legacy" and (claim_scope != "score_bin" or expected_market is not None):
        raise ValueError("claim_scope and expected_market are only used in claims_first mode")
    if publication_mode == "claims_first" and (
        type(expected_account_id) is not int or not isinstance(expected_period, str)
    ):
        raise ValueError("claims_first requires expected_account_id and expected_period")
    if publication_mode == "claims_first" and claim_scope in ("warning_signal", "score_bin_warning_signal", "decline_events", "score_bin_warning_signal_decline_events", "score_bin_warning_signal_no_declines", "score_bin_warning_signal_quality_hold", "grace_decline_events") and type(expected_market) is not int:
        raise ValueError("Policy-bearing claim scopes require expected_market from the caller")
    if publication_mode == "claims_first" and claim_scope in ("decline_events", "score_bin_warning_signal_decline_events", "score_bin_warning_signal_no_declines", "score_bin_warning_signal_quality_hold") and expected_market != 1:
        raise ValueError("Temporal claim scopes currently support market 1 only")

    if claim_scope == "grace_decline_events":
        if publication_mode != "claims_first" or expected_market == 1:
            raise ValueError("Grace decline reports require claims_first and a non-primary market")
        if (not isinstance(site_onboarded_period, str)
                or not isinstance(last_exception_review_period, str)):
            raise ValueError("Grace reports require trusted site onboarding and exception review dates")
    elif site_onboarded_period is not None or last_exception_review_period is not None:
        raise ValueError("Site metadata is supported only in grace_decline_events scope")

    # Caller-selected reporting context is an instruction about the target,
    # NOT evidence that any score movement or policy trigger occurred.
    policy_context = (
        f"\n\nRequested context: account_id={expected_account_id}, "
        f"as_of_period={expected_period}, market={expected_market}."
        if publication_mode == "claims_first" and claim_scope in ("warning_signal", "score_bin_warning_signal", "decline_events", "score_bin_warning_signal_decline_events", "score_bin_warning_signal_no_declines", "score_bin_warning_signal_quality_hold", "grace_decline_events")
        else ""
    )
    if claim_scope == "grace_decline_events":
        policy_context += (
            f" Trusted site metadata: onboarded={site_onboarded_period}, "
            f"last_exception_review={last_exception_review_period}."
        )
    messages = [
        {"role": "system", "content": (
            SYSTEM_PROMPT + "\n\n" + STRUCTURED_OUTPUT_INSTRUCTIONS
            + ("\n\n" + {
                "score_bin": CLAIM_FIRST_INSTRUCTIONS,
                "warning_signal": CLAIM_FIRST_POLICY_INSTRUCTIONS,
                "score_bin_warning_signal": CLAIM_FIRST_COMBINED_INSTRUCTIONS,
                "decline_events": CLAIM_FIRST_DECLINE_EVENTS_INSTRUCTIONS,
                "score_bin_warning_signal_decline_events": CLAIM_FIRST_FULL_NARRATIVE_INSTRUCTIONS,
                "score_bin_warning_signal_no_declines": CLAIM_FIRST_NO_DECLINES_INSTRUCTIONS,
                "score_bin_warning_signal_quality_hold": CLAIM_FIRST_QUALITY_HOLD_INSTRUCTIONS,
                "grace_decline_events": CLAIM_FIRST_GRACE_DECLINES_INSTRUCTIONS,
            }[claim_scope] if publication_mode == "claims_first" else "")
            + ("\n\n" + CONSECUTIVE_DECLINE_EVENT_COUNT_INSTRUCTIONS
               if publication_mode == "claims_first" and claim_scope in _COUNTED_EVENT_SCOPES
               else "")
            + ("\n\n" + CLAIM_FIRST_FULL_LIMITATIONS_INSTRUCTIONS
               if publication_mode == "claims_first"
               and claim_scope == "score_bin_warning_signal_decline_events"
               else "")
            + ("\n\n" + CLAIM_FIRST_QUALITY_HOLD_LIMITATIONS_INSTRUCTIONS
               if publication_mode == "claims_first"
               and claim_scope == "score_bin_warning_signal_quality_hold"
               else "")
            + policy_context
        )},
        {"role": "user", "content": question},
    ]

    response = llm.chat(messages=messages, tools=active_tools)
    evidence: list[dict] = []
    rounds = 0

    while response.tool_calls:
        if rounds >= max_tool_rounds:
            if verbose:
                print(f"\nAgent stopped at the {max_tool_rounds}-round safety limit.")
            return _blocked("TOOL_ROUND_LIMIT", evidence, rounds, completed=False)

        if verbose:
            print(f"\n=== TOOL ROUND {rounds + 1} ===")

        results = []
        for call in response.tool_calls:
            if verbose:
                arguments = ", ".join(
                    f"{key}={value!r}" for key, value in call.arguments.items()
                )
                print(f"  - {call.name}({arguments})")

            function = active_dispatch.get(call.name)
            if function is None:
                result = {"error": f"Unknown tool: {call.name}"}
            else:
                try:
                    result = function(**call.arguments)
                except (TypeError, ValueError) as exc:
                    result = {"error": f"Invalid tool arguments: {exc}"}

            results.append(result)
            evidence.append({
                "tool_call_id": call.id,
                "tool_name": call.name,
                "arguments": call.arguments,
                "result": result,
            })

            if verbose and isinstance(result, dict) and "error" in result:
                print(f"    ! {result['error']}")

        if verbose:
            print("  Sending tool results back to the model...")

        response = llm.continue_with_tool_results(
            assistant_response=response,
            tool_results=results,
            tools=active_tools,
        )
        rounds += 1

    if verbose:
        print("\n=== AGENT FINISHED ===")
        print(f"Tool rounds: {rounds}")
        print(f"Total tool calls: {len(evidence)}")

    # Tool execution errors cannot be silently ignored even if some other
    # evidence would support the final narrative.
    if any(isinstance(item["result"], dict) and "error" in item["result"]
           for item in evidence):
        return _blocked("TOOL_EXECUTION_ERROR", evidence, rounds, completed=True)

    try:
        if publication_mode == "legacy":
            audit = verify_structured_response(response.text, evidence)
            decision = handle_verification(audit)
        else:
            # The draft narrative is *never* returned in claim-first mode.
            verify_kwargs = {}
            if claim_scope == "grace_decline_events":
                verify_kwargs = {
                    "site_onboarded_period": site_onboarded_period,
                    "last_exception_review_period": last_exception_review_period,
                }
            audit = verify_structured_response(
                response.text, evidence, narrative_mode="claims_first",
                **verify_kwargs,
            )
            if audit["status"] == "passed":
                try:
                    if claim_scope == "score_bin":
                        trusted_answer = compose_verified_score_bin(
                            audit, account_id=expected_account_id,
                            period=expected_period,
                        )
                        rendering_method = "verified_score_bin"
                    elif claim_scope == "warning_signal":
                        trusted_answer = compose_verified_warning_signal(
                            audit, account_id=expected_account_id,
                            as_of_period=expected_period, market=expected_market,
                        )
                        rendering_method = "verified_warning_signal"
                    elif claim_scope == "score_bin_warning_signal":
                        trusted_answer = compose_verified_score_bin_warning_signal(
                            audit, account_id=expected_account_id,
                            period=expected_period, market=expected_market,
                        )
                        rendering_method = "verified_score_bin_warning_signal"
                    elif claim_scope == "decline_events":
                        trusted_answer = compose_verified_decline_event_narrative(
                            audit, evidence, account_id=expected_account_id,
                            as_of_period=expected_period, market=expected_market,
                        )
                        rendering_method = "verified_decline_events"
                    elif claim_scope == "score_bin_warning_signal_decline_events":
                        trusted_answer = compose_verified_full_narrative(
                            audit, evidence, account_id=expected_account_id,
                            as_of_period=expected_period, market=expected_market,
                        )
                        rendering_method = "verified_full_temporal_narrative"
                    elif claim_scope == "score_bin_warning_signal_no_declines":
                        trusted_answer = compose_verified_no_decline_narrative(
                            audit, evidence, account_id=expected_account_id,
                            as_of_period=expected_period, market=expected_market,
                        )
                        rendering_method = "verified_no_decline_narrative"
                    elif claim_scope == "grace_decline_events":
                        trusted_answer = compose_verified_grace_declines(
                            audit, evidence, account_id=expected_account_id,
                            as_of_period=expected_period, market=expected_market,
                        )
                        rendering_method = "verified_grace_decline_events"
                    else:  # verified pending-DQ or an explicit insufficient-evidence block
                        trusted_answer = compose_verified_quality_hold_narrative(
                            audit, evidence, account_id=expected_account_id,
                            as_of_period=expected_period, market=expected_market,
                        )
                        rendering_method = "verified_quality_hold_narrative"
                except InsufficientPolicyEvidenceError as exc:
                    audit["rendering"] = {"status": "incomplete", "reason": str(exc)}
                    return _blocked("POLICY_EVIDENCE_INSUFFICIENT", evidence, rounds,
                                    completed=True, next_action="gather_evidence", audit=audit)
                except (CompositionError, TemporalNarrativeError, CombinedTemporalNarrativeError,
                        ZeroDeclineNarrativeError, QualityHoldNarrativeError,
                        GraceDeclineNarrativeError) as exc:
                    audit["rendering"] = {"status": "incomplete", "reason": str(exc)}
                    return _blocked("COMPOSITION_UNSUPPORTED", evidence, rounds,
                                    completed=True, next_action="revise_response", audit=audit)
                audit["rendering"] = {"status": "passed", "method": rendering_method}
                # Supply the gate with the *trusted* rendered answer, never the
                # draft text. Narrative coverage is established by construction.
                gate_audit = {
                    **audit,
                    "answer": trusted_answer,
                    "narrative": {
                        "status": "passed", "coverage_complete": True,
                        "checks": [], "unchecked_text": [],
                        "omitted_score_claims": [],
                        "coverage_method": "deterministic_verified_renderer",
                    },
                }
                decision = handle_verification(gate_audit)
            else:
                # Preserve genuine failed/incomplete claim status; do not turn a
                # missing observation into a fabricated narrative mismatch.
                gate_audit = {
                    **audit,
                    "answer": "Unpublished claim-first response",
                    "narrative": {
                        "status": "passed", "coverage_complete": True,
                        "checks": [], "unchecked_text": [],
                        "omitted_score_claims": [],
                        "coverage_method": "draft_excluded_not_rendered",
                    },
                }
                decision = handle_verification(gate_audit)
                if decision["publishable"]:
                    return _blocked("INCONSISTENT_AUDIT", evidence, rounds,
                                    completed=True, audit=audit)
    except Exception as exc:
        # A verification bug must NEVER turn an unverified draft into a
        # publishable narrative. Propagate a diagnostic category, not draft.
        if verbose:
            print(f"Verification could not finish: {type(exc).__name__}: {exc}")
        return _blocked("VERIFICATION_ERROR", evidence, rounds, completed=True)

    # A policy claim can be mathematically correct yet not be grounded in any
    # policy passage. For the hybrid RAG workflow require at least one actual
    # retrieval hit before releasing a policy-bearing answer. This proves a
    # passage was retrieved, NOT that it semantically supports every sentence.
    if decision["publishable"] and audit["policy"]["status"] != "not_applicable":
        has_policy_passage = any(
            item["tool_name"] == "retrieve_policy"
            and isinstance(item["result"], list)
            and any(isinstance(hit, dict) and hit.get("source") and hit.get("text")
                    for hit in item["result"])
            for item in evidence
        )
        if claim_scope in ("decline_events", "score_bin_warning_signal_decline_events", "score_bin_warning_signal_no_declines", "score_bin_warning_signal_quality_hold") and publication_mode == "claims_first":
            # This renderer explicitly attributes the event to POL-002. A hit
            # from an unrelated policy memo must not satisfy this requirement.
            has_policy_passage = any(
                item["tool_name"] == "retrieve_policy"
                and isinstance(item["result"], list)
                and any(isinstance(hit, dict)
                        and isinstance(hit.get("source"), str)
                        and hit["source"].startswith("POL-002")
                        and isinstance(hit.get("text"), str)
                        and bool(hit["text"].strip())
                        for hit in item["result"])
                for item in evidence
            )
        if claim_scope == "score_bin_warning_signal_quality_hold" and publication_mode == "claims_first":
            # The public explanation explicitly attributes the quality hold
            # to POL-005, not merely to the presence of *some* policy passage.
            has_pol005 = any(
                item["tool_name"] == "retrieve_policy"
                and isinstance(item["result"], list)
                and any(isinstance(hit, dict)
                        and isinstance(hit.get("source"), str)
                        and hit["source"].startswith("POL-005")
                        and isinstance(hit.get("text"), str)
                        and bool(hit["text"].strip())
                        for hit in item["result"])
                for item in evidence
            )
            has_policy_passage = has_policy_passage and has_pol005
        if claim_scope == "grace_decline_events" and publication_mode == "claims_first":
            retrieved = {
                hit["source"][:7]
                for item in evidence
                if item["tool_name"] == "retrieve_policy" and isinstance(item["result"], list)
                for hit in item["result"]
                if isinstance(hit, dict) and isinstance(hit.get("source"), str)
                and isinstance(hit.get("text"), str) and hit["text"].strip()
            }
            has_policy_passage = {"POL-002", "POL-004"}.issubset(retrieved)
            # A held event cites POL-005 explicitly; require that memo too.
            if audit.get("rendering", {}).get("status") == "passed":
                if "pending data-quality review under POL-005" in decision["public_answer"]:
                    has_policy_passage = has_policy_passage and "POL-005" in retrieved
        if not has_policy_passage:
            return _blocked("POLICY_RETRIEVAL_MISSING", evidence, rounds,
                            completed=True, next_action="gather_evidence", audit=audit)

    if verbose:
        print(f"Verification status: {decision['status']}")
        print(f"Publishable: {decision['publishable']}")
        if not decision["publishable"]:
            print(f"Next action: {decision['next_action']}")

    return _result(decision, evidence, rounds, completed=True, audit=audit)
