from dataclasses import dataclass
from datetime import date
from typing import Literal


@dataclass(frozen=True)
class VerifiedFinding:
    """A verified policy or temporal finding for narrative rendering."""
    kind: Literal["temporal", "policy"]
    field: str
    value: str | int | float | bool
    account_id: int
    as_of_period: str
    market: int
    event_period: str | None = None
    evidence_periods: tuple[str, ...] = ()
    policy_ids: tuple[str, ...] = ()


class FindingError(ValueError):
    """The audit does not authorize a verified narrative finding."""


def extract_verified_policy_findings(
    audit: dict, *, account_id: int, as_of_period: str, market: int
) -> tuple[VerifiedFinding, ...]:
    """Convert narrowly supported, passed policy checks into verified findings.

    The supplied account/date/market must come from the caller, not the LLM.
    This adapter does not re-run the validator or infer event/policy citations.
    """
    if type(account_id) is not int or type(market) is not int:
        raise FindingError("Caller must supply integer account and market IDs")
    try:
        if date.fromisoformat(as_of_period).isoformat() != as_of_period:
            raise ValueError("non-canonical date")
    except (TypeError, ValueError) as exc:
        raise FindingError("Caller must supply an ISO reporting date") from exc

    if (
        not isinstance(audit, dict)
        or audit.get("status") != "passed"
        or audit.get("schema", {}).get("status") != "passed"
        or audit.get("issues")
        or audit.get("limitations")
        or audit.get("narrative", {}).get("coverage_method") != "llm_draft_excluded"
    ):
        raise FindingError("The overall claim-first audit is not publishable")

    numeric = audit.get("numeric", {})
    if numeric.get("status", "not_applicable") not in {"passed", "not_applicable"}:
        raise FindingError("Numerical audit is not publishable")
    if any(
        not isinstance(c, dict)
        or c.get("status") != "passed"
        or c.get("context_grounded") is not True
        or c.get("value_grounded") is not True
        for c in numeric.get("checks", [])
    ):
        raise FindingError("Numerical audit contains an ungrounded claim")

    policy = audit.get("policy", {})
    checks = policy.get("checks")
    if policy.get("status") != "passed" or not isinstance(checks, list) or not checks:
        raise FindingError("No verified policy checks to convert")

    findings = []
    for check in checks:
        if not isinstance(check, dict) or check.get("status") != "passed":
            raise FindingError("A policy check did not pass")
        claim = check.get("claim")
        inner = check.get("audit")
        if not isinstance(claim, dict) or not isinstance(inner, dict):
            raise FindingError("Missing claim or validator audit")
        if claim.get("field") != "warning_signal_status":
            raise FindingError("Unsupported policy finding field")
        if (
            type(claim.get("account_id")) is not int
            or type(claim.get("market")) is not int
            or (claim["account_id"], claim.get("as_of_period"), claim["market"])
            != (account_id, as_of_period, market)
        ):
            raise FindingError("Claim context does not match caller context")
        inner_checks = inner.get("checks")
        if inner.get("status") != "passed" or not isinstance(inner_checks, list) or len(inner_checks) != 1:
            raise FindingError("No single passed validator verdict")
        verdict = inner_checks[0]
        if not isinstance(verdict, dict) or not (
            verdict.get("passed") is True
            and verdict.get("context_matches") is True
            and verdict.get("value_matches") is True
            and verdict.get("field") == claim["field"]
            and verdict.get("claimed") == claim.get("value")
            and verdict.get("expected") == claim.get("value")
        ):
            raise FindingError("Validator evidence does not support claim")
        findings.append(VerifiedFinding(
            kind="policy", field=claim["field"], value=claim["value"],
            account_id=account_id, as_of_period=as_of_period, market=market,
        ))

    return tuple(findings)
