"""Module 5.4.1: parse and validate the structure of an LLM's final response.

IMPORTANT: This validates structure, not factual/policy truth.
Existing guardrails must check the extracted claims against tool evidence.
"""

import json
from datetime import date
from math import isfinite


class StructuredOutputError(ValueError):
    """Model response is missing required information or has an invalid shape."""


def _require_keys(value: object, keys: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        raise StructuredOutputError(f"{label} must contain exactly {sorted(keys)}")


def _integer(value: object) -> bool:
    return type(value) is int  # Reject booleans, which are int subclasses.


def _iso_date(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _simple_value(value: object) -> bool:
    return (
        type(value) in (str, int, bool)
        or (type(value) is float and isfinite(value))
    )


def parse_structured_output(raw: str) -> dict:
    """Parse a structured LLM response. Reject malformed or missing claims lists."""
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise StructuredOutputError("The model did not return valid JSON") from exc

    _require_keys(payload, {"answer", "score_claims", "policy_claims", "limitations"}, "Response")
    if not isinstance(payload["answer"], str) or not payload["answer"].strip():
        raise StructuredOutputError("answer must be a non-empty string")
    if not isinstance(payload["limitations"], list) or any(
        not isinstance(x, str) or not x.strip() for x in payload["limitations"]
    ):
        raise StructuredOutputError("limitations must be a list of non-empty strings")

    for group in ("score_claims", "policy_claims"):
        if not isinstance(payload[group], list):
            raise StructuredOutputError(f"{group} must be a list (possibly empty)")

    for i, claim in enumerate(payload["score_claims"]):
        _require_keys(claim, {"account_id", "period", "metric", "value", "market"}, f"score_claims[{i}]")
        if (
            not _integer(claim["account_id"])
            or not _integer(claim["market"])
            or not _integer(claim["value"])
            or claim["metric"] not in ("score", "bin")
            or not _iso_date(claim["period"])
        ):
            raise StructuredOutputError(f"score_claims[{i}] has invalid types or values")

    for i, claim in enumerate(payload["policy_claims"]):
        _require_keys(claim, {"field", "value", "account_id", "as_of_period", "market"}, f"policy_claims[{i}]")
        if (
            not isinstance(claim["field"], str)
            or not claim["field"].strip()
            or not _simple_value(claim["value"])
            or not _integer(claim["account_id"])
            or not _integer(claim["market"])
            or not _iso_date(claim["as_of_period"])
        ):
            raise StructuredOutputError(f"policy_claims[{i}] has invalid types or values")

    return payload
