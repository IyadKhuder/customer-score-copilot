"""Module 5.4.2: orchestrate existing numerical and policy guardrails.

This module does NOT compute policy thresholds or replace the validators.
It audits only evidence from the agent's tool calls. The legacy narrative
checker is a best-effort coverage check, not semantic proof of completeness.
"""

import re

from src.structured_output import StructuredOutputError, parse_structured_output


# Two observed real-provider phrasings (present or past tense, optional source
# phrase). This is NOT a general semantic validator. Fullmatch + independently
# validated score/bin claims ensure the exception remains narrowly scoped.
_SCORE_BIN_SENTENCE = re.compile(
    r"For account (?P<account_id>\d+) on "
    r"(?P<period>\d{4}-\d{2}-\d{2}), "
    r"the score (?P<tense>is|was) (?P<score>\d+) and "
    r"the risk bin (?P=tense) (?P<bin>\d+) "
    r"in market (?P<market>\d+)"
    r"(?:, based on the account history retrieved)?\.",
    flags=re.IGNORECASE,
)


def _audit_exact_score_bin_sentence(
    answer: str,
    score_claims: list[dict],
    policy_claims: list[dict],
    numeric_checks: list[dict],
    legacy_audit: dict,
) -> dict | None:
    """Recognize ONE completely covered numerical sentence when legacy regex misses.

    Fail closed for different prose, extra material, policy claims, absent claims,
    contradictions, or missing evidence. The numerical validator remains the
    sole authority on whether the scores/bins are actually correct.
    """
    if (
        policy_claims
        or len(score_claims) != 2
        or len(numeric_checks) != 2
        or legacy_audit.get("status") != "incomplete"
        or legacy_audit.get("coverage_complete") is not False
        or legacy_audit.get("checks") != []
        or legacy_audit.get("unchecked_text") != [answer]
    ):
        return None

    match = _SCORE_BIN_SENTENCE.fullmatch(answer)
    if match is None:
        return None

    values = match.groupdict()
    account_id = int(values["account_id"])
    market = int(values["market"])
    period = values["period"]
    expected_claims = {
        (account_id, period, "score", int(values["score"]), market),
        (account_id, period, "bin", int(values["bin"]), market),
    }
    actual_claims = {
        (c["account_id"], c["period"], c["metric"], c["value"], c["market"])
        for c in score_claims
    }
    if actual_claims != expected_claims or any(
        check.get("status") != "passed" or check.get("context_grounded") is not True
        for check in numeric_checks
    ):
        return None

    return {
        "status": "passed",
        "coverage_complete": True,
        "checks": [
            {"claim": check["claim"], "status": "passed", "context_grounded": True}
            for check in numeric_checks
        ],
        "unchecked_text": [],
        "coverage_method": "strict_score_bin_sentence_v2",
    }



def _combine(*statuses: str) -> str:
    """A known contradiction outranks an information gap."""
    if "failed" in statuses:
        return "failed"
    if "incomplete" in statuses:
        return "incomplete"
    return "passed"


def _histories_from_evidence(tool_evidence: list[dict]) -> dict[int, list[dict]]:
    """Use recorded get_history outputs, not a fresh/unlogged database query."""
    histories = {}
    for item in tool_evidence:
        if item.get("tool_name") != "get_history":
            continue
        account = item.get("arguments", {}).get("account_id")
        rows = item.get("result")
        if type(account) is int and isinstance(rows, list) and rows and all(
            isinstance(row, dict)
            and isinstance(row.get("period"), str)
            and type(row.get("market")) is int
            and type(row.get("score")) is int
            and type(row.get("bin")) is int
            for row in rows
        ):
            histories[account] = rows
    return histories


def verify_structured_response(
    raw: str, tool_evidence: list[dict], *, narrative_mode: str = "legacy",
    site_onboarded_period: str | None = None,
    last_exception_review_period: str | None = None,
) -> dict:
    """Return a conservative, per-layer audit of the model's final JSON.

    No database or LLM calls are made here. Existing validators decide whether
    a score or policy claim is true; this function only coordinates them.
    """
    if narrative_mode not in ("legacy", "claims_first"):
        raise ValueError("narrative_mode must be 'legacy' or 'claims_first'")

    result = {
        "status": "incomplete",
        "answer": None,
        "schema": {"status": "incomplete"},
        "numeric": {"status": "not_applicable", "checks": []},
        "policy": {"status": "not_applicable", "checks": []},
        "narrative": {"status": "incomplete", "coverage_complete": False},
        "limitations": [],
        "issues": [],
    }

    try:
        data = parse_structured_output(raw)
    except StructuredOutputError as exc:
        result["issues"].append(f"Invalid structured output: {exc}")
        return result

    result["schema"] = {"status": "passed"}
    result["answer"] = data["answer"]
    result["limitations"] = data["limitations"]

    # Imports inside the function keep this module independent of provider SDKs
    # and allow its orchestration to be unit-tested with test doubles.
    try:
        from src.guardrail import ScoreClaim, check_score_claim, extract_score_claims, audit_answer
        from src.policy_claim_guard import audit_policy_claims
    except ImportError as exc:
        result["issues"].append(f"Validator unavailable: {exc}")
        return result

    histories = _histories_from_evidence(tool_evidence)

    # 1. Verify explicit scores/bins against recorded numerical tool results.
    if data["score_claims"]:
        statuses = []
        for payload in data["score_claims"]:
            claim = ScoreClaim(**payload)
            if claim.account_id not in histories or not any(
                row["period"] == claim.period for row in histories[claim.account_id]
            ):
                verdict = {"value_grounded": False, "context_grounded": False}
                status = "incomplete"  # No relevant observation is not a contradiction.
            else:
                try:
                    verdict = check_score_claim(claim, tool_evidence)
                    status = "passed" if verdict.get("context_grounded") is True else "failed"
                except Exception as exc:
                    verdict = {"error": str(exc)}
                    status = "incomplete"
            statuses.append(status)
            result["numeric"]["checks"].append({
                "claim": payload, "status": status, **verdict,
            })
        result["numeric"]["status"] = _combine(*statuses)

    # 2. Verify explicit policy/temporal claims with the EXISTING policy engine.
    if data["policy_claims"]:
        statuses = []
        for payload in data["policy_claims"]:
            account, period, market = (
                payload["account_id"], payload["as_of_period"], payload["market"]
            )
            history = histories.get(account)
            if history is None or not any(row["period"] == period for row in history):
                status = "incomplete"
                detail = {"reason": "No get_history evidence for account and as_of_period"}
            elif any(row["market"] != market for row in history):
                status = "failed"
                detail = {"reason": "Claimed market conflicts with account history"}
            else:
                try:
                    # Each claim has its own account and reporting-period context.
                    # Never let a calculation use score observations after
                    # the claim's reporting period (temporal leakage).
                    as_of_history = [row for row in history if row["period"] <= period]
                    policy_kwargs = {}
                    if site_onboarded_period is not None:
                        policy_kwargs["site_onboarded_period"] = site_onboarded_period
                    if last_exception_review_period is not None:
                        policy_kwargs["last_exception_review_period"] = last_exception_review_period
                    detail = audit_policy_claims(
                        [payload], as_of_history, account_id=account,
                        as_of_period=period, **policy_kwargs
                    )
                    status = detail.get("status", "incomplete")
                    if status not in ("passed", "failed", "incomplete"):
                        status = "incomplete"
                    if status == "passed":
                        checks = detail.get("checks")
                        if (not isinstance(checks, list) or len(checks) != 1
                                or checks[0].get("passed") is not True):
                            status = "incomplete"
                except Exception as exc:
                    status = "incomplete"
                    detail = {"error": str(exc)}
            statuses.append(status)
            result["policy"]["checks"].append({
                "claim": payload, "status": status, "audit": detail,
            })
        result["policy"]["status"] = _combine(*statuses)

    # Claim-first mode never attempts to interpret unpublished LLM prose.
    # A deterministic composer will produce the only publishable wording.
    if narrative_mode == "claims_first":
        result["narrative"] = {
            "status": "not_applicable",
            "coverage_complete": False,
            "coverage_method": "llm_draft_excluded",
            "checks": [],
            "unchecked_text": [],
            "omitted_score_claims": [],
        }
    else:
        # 3. Best-effort legacy scan: detect numeric claims the model omitted
        #    from score_claims, and honor narrative audit failures/gaps.
        try:
            narrative = audit_answer(data["answer"], tool_evidence)
            # The legacy extractor can miss a valid score+bin sentence with
            # different phrasing. Handle ONLY these observed forms, and ONLY
            # after both explicit numerical claims pass.
            narrow_audit = _audit_exact_score_bin_sentence(
                answer=data["answer"],
                score_claims=data["score_claims"],
                policy_claims=data["policy_claims"],
                numeric_checks=result["numeric"]["checks"],
                legacy_audit=narrative,
            )
            if narrow_audit is not None:
                narrative = narrow_audit
            parsed_prose_claims = extract_score_claims(data["answer"])
            structured_claim_keys = {
                (c["account_id"], c["period"], c["metric"], c["value"], c["market"])
                for c in data["score_claims"]
            }
            omitted = [
                {"account_id": c.account_id, "period": c.period,
                 "metric": c.metric, "value": c.value, "market": c.market}
                for c in parsed_prose_claims
                if (c.account_id, c.period, c.metric, c.value, c.market)
                not in structured_claim_keys
            ]
            narrative_status = narrative.get("status", "incomplete")
            if narrative_status not in ("passed", "failed", "incomplete"):
                narrative_status = "incomplete"
            if narrative.get("coverage_complete") is not True or omitted:
                narrative_status = _combine(narrative_status, "incomplete")
            result["narrative"] = {
                **narrative, "status": narrative_status, "omitted_score_claims": omitted
            }
            if omitted:
                result["issues"].append("Narrative contains score claims missing from score_claims")
        except Exception as exc:
            result["narrative"] = {"status": "incomplete", "coverage_complete": False,
                                   "error": str(exc)}

    if not data["score_claims"] and not data["policy_claims"]:
        result["issues"].append("No explicit claims to verify")

    statuses = ([result["narrative"]["status"]] if narrative_mode == "legacy" else [])
    for section in ("numeric", "policy"):
        if result[section]["status"] != "not_applicable":
            statuses.append(result[section]["status"])
    if not data["score_claims"] and not data["policy_claims"]:
        statuses.append("incomplete")
    result["status"] = _combine(*statuses)
    return result
