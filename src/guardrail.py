
"""Module 5.1 — Score and bin grounding checks."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ScoreClaim:
    account_id: int
    period: str
    metric: str
    value: int
    market: int | None = None


def check_score_claim(
    claim: ScoreClaim,
    tool_evidence: list[dict],
) -> dict:
    """Check numeric existence and contextual association."""

    if claim.metric not in {"score", "bin"}:
        raise ValueError(f"Unsupported metric: {claim.metric}")

    value_found = False
    relation_found = False

    for call in tool_evidence:
        if call.get("tool_name") != "get_history":
            continue

        account_id = call.get("arguments", {}).get("account_id")
        rows = call.get("result")

        if not isinstance(rows, list):
            continue

        for row in rows:
            if not isinstance(row, dict):
                continue

            if row.get(claim.metric) != claim.value:
                continue

            value_found = True

            if (
                account_id == claim.account_id
                and row.get("period") == claim.period
                and (
                    claim.market is None
                    or row.get("market") == claim.market
                )
            ):
                relation_found = True

    return {
        "value_grounded": value_found,
        "context_grounded": relation_found,
    }



# Module 5.2 — Extract claims from natural-language answers

CLAIM_PATTERN = re.compile(
    r"\bAccount\s+(?P<account_id>\d+)"
    r"\s+(?:had|has)\s+a\s+"
    r"(?P<metric>score|bin)\s+of\s+"
    r"(?P<value>\d+)\s+on\s+"
    r"(?P<period>\d{4}-\d{2}-\d{2})"
    r"(?:\s+in\s+market\s+(?P<market>\d+))?\b",
    flags=re.IGNORECASE,
)


def extract_score_claims(answer: str) -> list[ScoreClaim]:
    """Extract score/bin claims matching our supported sentence pattern."""

    claims = []

    for match in CLAIM_PATTERN.finditer(answer):
        market = match.group("market")

        claim = ScoreClaim(
            account_id=int(match.group("account_id")),
            period=match.group("period"),
            metric=match.group("metric").lower(),
            value=int(match.group("value")),
            market=int(market) if market is not None else None,
        )

        claims.append(claim)

    return claims



def audit_answer(answer: str, tool_evidence: list[dict]) -> dict:
    """
    Validate recognized claims and identify unchecked text.

    A 'passed' result applies only to our supported
    sentence format, not arbitrary natural language.
    """

    # Step 1: Find all recognized claim spans.
    matches = list(CLAIM_PATTERN.finditer(answer))
    claims = extract_score_claims(answer)

    # Step 2: Collect text not covered by the regex.
    unchecked_text = []
    cursor = 0

    for match in matches:
        fragment = answer[cursor:match.start()].strip()

        # Ignore whitespace and simple punctuation only.
        if fragment.strip(" \t\n\r.,;:!?"):
            unchecked_text.append(fragment)

        cursor = match.end()

    # Don't forget the text after the last match.
    fragment = answer[cursor:].strip()
    if fragment.strip(" \t\n\r.,;:!?"):
        unchecked_text.append(fragment)

    # Step 3: Validate each extracted claim.
    checks = [
        {
            "claim": claim,
            **check_score_claim(claim, tool_evidence),
        }
        for claim in claims
    ]

    # Step 4: Determine the overall result.
    coverage_complete = bool(claims) and not unchecked_text

    if any(not check["context_grounded"] for check in checks):
        status = "failed"
    elif not coverage_complete:
        status = "incomplete"
    else:
        status = "passed"

    return {
        "status": status,
        "coverage_complete": coverage_complete,
        "checks": checks,
        "unchecked_text": unchecked_text,
    }
