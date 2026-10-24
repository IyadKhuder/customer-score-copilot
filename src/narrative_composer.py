"""Deterministic narrative rendering for independently verified claims.

The Module 5.4 numerical composer remains numeric-only. Module 5.5.3 adds a
separate, deliberately narrow policy composer; neither trusts LLM prose.
"""

from datetime import date

from src.narrative_findings import FindingError, extract_verified_policy_findings


class CompositionError(ValueError):
    """Verified claims cannot safely produce the requested factual answer."""


def compose_verified_score_bin(audit: dict, *, account_id: int, period: str) -> str:
    """Create a factual sentence from verified score AND bin checks.

    The caller must supply expected account/period independently of LLM output.
    Only validator-confirmed checks from the pipeline may reach this function.
    Unsupported policy claims, extra facts, and limitations block publication.
    """
    if type(account_id) is not int or not isinstance(period, str):
        raise CompositionError("Expected account and reporting period are required")
    try:
        if date.fromisoformat(period).isoformat() != period:
            raise ValueError("not ISO")
    except ValueError as exc:
        raise CompositionError("Expected period must be a valid ISO date") from exc

    if not isinstance(audit, dict) or audit.get("schema", {}).get("status") != "passed":
        raise CompositionError("Structured response was not validated")
    if audit.get("status") != "passed":
        raise CompositionError("Claim verification has not passed")

    policy = audit.get("policy", {})
    if policy.get("status") != "not_applicable" or policy.get("checks") != []:
        raise CompositionError("Policy claims are outside this numeric-only composer")
    if audit.get("limitations"):
        raise CompositionError("Nonempty limitations require a broader response composer")
    if audit.get("issues"):
        raise CompositionError("Unresolved audit issues prevent composition")

    numeric = audit.get("numeric", {})
    checks = numeric.get("checks")
    if numeric.get("status") != "passed" or not isinstance(checks, list) or len(checks) != 2:
        raise CompositionError("One verified score and one verified bin are required")

    claims = {}
    for check in checks:
        if (not isinstance(check, dict)
            or check.get("status") != "passed"
            or check.get("context_grounded") is not True
            or check.get("value_grounded") is not True):
            raise CompositionError("A numerical claim is not fully grounded")
        claim = check.get("claim")
        if not isinstance(claim, dict) or set(claim) != {
            "account_id", "period", "metric", "value", "market"
        }:
            raise CompositionError("Invalid verified claim structure")
        if claim["account_id"] != account_id or claim["period"] != period:
            raise CompositionError("A claim does not match the requested account and date")
        if (type(claim["value"]) is not int
            or type(claim["market"]) is not int
            or claim["metric"] not in {"score", "bin"}
            or claim["metric"] in claims):
            raise CompositionError("Unsupported or duplicated numerical claim")
        claims[claim["metric"]] = claim

    if set(claims) != {"score", "bin"}:
        raise CompositionError("Both score and bin must be independently verified")
    if claims["score"]["market"] != claims["bin"]["market"]:
        raise CompositionError("Score and bin refer to different markets")

    return (
        f"For account {account_id} on {period}, "
        f"the verified score was {claims['score']['value']} "
        f"and the risk bin was {claims['bin']['value']} "
        f"in market {claims['score']['market']}."
    )


def compose_verified_warning_signal(
    audit: dict, *, account_id: int, as_of_period: str, market: int
) -> str:
    """Render one independently verified early-warning policy signal.

    Only `warning_signal_status == "triggered"` is supported in this module.
    Re-checks the full internal audit through the Module 5.5.2 adapter, not
    through user/LLM text or a caller-constructed VerifiedFinding.

    Intentionally refuses mixed score-policy responses, other policy fields,
    and claims for multiple accounts. It does not infer an underlying event,
    cite a policy ID, or claim that an operational flag was actually logged.
    """
    if not isinstance(audit, dict):
        raise CompositionError("A verification audit is required")

    # Our current numeric composer cannot render policy claims. Likewise,
    # this policy-only composer must not silently omit numerical claims.
    numeric = audit.get("numeric", {"status": "not_applicable", "checks": []})
    if (
        not isinstance(numeric, dict)
        or numeric.get("status") != "not_applicable"
        or numeric.get("checks") != []
    ):
        raise CompositionError("Mixed numerical-policy rendering is not supported yet")

    policy = audit.get("policy", {})
    if not isinstance(policy, dict) or not isinstance(policy.get("checks"), list):
        raise CompositionError("Policy verification checks are required")
    if len(policy["checks"]) != 1:
        raise CompositionError("Exactly one policy claim is supported")

    try:
        findings = extract_verified_policy_findings(
            audit, account_id=account_id,
            as_of_period=as_of_period, market=market,
        )
    except FindingError as exc:
        raise CompositionError("Policy claim is not safely verified") from exc

    if len(findings) != 1:
        raise CompositionError("Exactly one verified finding is required")

    finding = findings[0]
    if (
        finding.kind != "policy"
        or finding.field != "warning_signal_status"
        or type(finding.value) is not str
        or finding.value != "triggered"
        or finding.event_period is not None
        or finding.evidence_periods
        or finding.policy_ids
    ):
        raise CompositionError("Unsupported policy finding or unverified details")

    return (
        f"As of {as_of_period}, the verified early-warning signal "
        f"for account {account_id} in market {market} was triggered. "
        "This is a policy-evaluated signal, not confirmation that "
        "a warning was entered in an operational register."
    )


def compose_verified_score_bin_warning_signal(
    audit: dict, *, account_id: int, period: str, market: int
) -> str:
    """Combine exactly two verified observations and one policy signal.

    The original audit must pass in full before preparing local *views* for the
    existing single-purpose composers. These views do not alter the audit, and
    they are never re-used as stand-alone verification results.

    No LLM prose, event explanation, extra policy claims, or warning-register
    assertion is published. Unsupported combinations fail closed.
    """
    if type(market) is not int:
        raise CompositionError("The requested market must be an integer")
    if not isinstance(audit, dict) or audit.get("status") != "passed":
        raise CompositionError("The full verification audit must pass")
    if audit.get("schema", {}).get("status") != "passed":
        raise CompositionError("Structured response was not validated")
    if audit.get("issues") or audit.get("limitations"):
        raise CompositionError("Unresolved issues or limitations block composition")
    if audit.get("narrative", {}).get("coverage_method") != "llm_draft_excluded":
        raise CompositionError("Only claim-first audits are supported")

    numeric = audit.get("numeric")
    policy = audit.get("policy")
    if (
        not isinstance(numeric, dict)
        or numeric.get("status") != "passed"
        or not isinstance(numeric.get("checks"), list)
        or len(numeric["checks"]) != 2
        or not isinstance(policy, dict)
        or policy.get("status") != "passed"
        or not isinstance(policy.get("checks"), list)
        or len(policy["checks"]) != 1
    ):
        raise CompositionError("Exactly two score/bin and one policy claim must pass")

    # The numerical composer already verifies each metric, reporting date, and
    # agreement between the markets. The extra caller-market check below also
    # binds BOTH numerical claims to the separately verified policy context.
    numeric_view = {
        **audit, "policy": {"status": "not_applicable", "checks": []},
    }
    numeric_text = compose_verified_score_bin(
        numeric_view, account_id=account_id, period=period,
    )
    for check in numeric["checks"]:
        if type(check["claim"]["market"]) is not int or check["claim"]["market"] != market:
            raise CompositionError("Numerical claim market differs from requested market")

    # The policy composer in turn invokes the verified-finding adapter and
    # checks the same account, date, market and exact supported policy value.
    policy_view = {
        **audit, "numeric": {"status": "not_applicable", "checks": []},
    }
    policy_text = compose_verified_warning_signal(
        policy_view, account_id=account_id, as_of_period=period, market=market,
    )
    return numeric_text + "\n\n" + policy_text
