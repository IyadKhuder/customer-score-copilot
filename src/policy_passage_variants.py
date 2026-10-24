"""Experimental, versioned synthetic policy passages for live evaluation.

The 'baseline' is deliberately identical to Module 6.2. The 'clarified_v1'
variant expands the *general rules* implemented by src.policy_decisions;
it never exposes the six benchmark cases or their expected outcomes.

This is an evidence-condition experiment, not a policy-rule change, RAG
retrieval evaluation, or endorsement of these rules for actual lending.
"""
from __future__ import annotations

import copy

from src.evaluation_runner import _SCRIPTED_POLICY_DOCS

POLICY_PASSAGE_VARIANTS = ("baseline", "clarified_v1")

_CLARIFIED_TEXT = {
    "POL-002": (
        "Synthetic POL-002 evaluation rules: A standard-market consecutive-decline "
        "event requires THREE downward monthly transitions across FOUR distinct, "
        "consecutive monthly score observations, without missing months. A qualifying "
        "event is dated at the FINAL observation in its declining streak. The rolling "
        "three-calendar-month evaluation window contains the requested reporting month "
        "and the two previous months. Count qualifying EVENT ENDING MONTHS inside this "
        "window, not the individual score decreases. Older baseline observations can "
        "support an event ending inside the window. An improvement in a later month "
        "does not erase a prior eligible event still in the window. Longer uninterrupted "
        "declines can generate overlapping events at separate ending months. Other "
        "early-warning criteria include a >=40 point single-month drop, a >=2 downward "
        "bin movement, or a six-month population standard deviation >35. The signal is "
        "triggered if an eligible event is found. With no eligible event, a clean "
        "not_triggered decision requires complete eight-month baseline coverage; "
        "otherwise the status is insufficient_evidence. Data-quality-affected events "
        "are pending review rather than eligible. None of these conclusions establishes "
        "a warning's administrative registration or clearance."
    ),
    "POL-004": (
        "Synthetic POL-004 exception rules: Market 1 uses standard thresholds. "
        "Other markets within their first 12 calendar months since trusted site "
        "onboarding have numeric thresholds raised by 25 percent. Three standard "
        "downward transitions therefore become FOUR consecutive transitions over "
        "FIVE observations (integer threshold rounded upward). The rolling evaluation "
        "window still uses the three most recent event-ending months. Quarterly site "
        "exception review must be current; it is not derivable from score history. "
        "Trusted site-onboarding and last-review metadata must be provided separately."
    ),
    "POL-005": (
        "Synthetic POL-005 data-quality rules: Large single-period score changes "
        "over 150 points and potential reversal artifacts warrant quality review. "
        "The current deterministic implementation automatically flags EXACT "
        "reversals within one or two months; near-equal reversal tolerance is not "
        "numerically defined in this experimental rule set. A qualifying event "
        "overlapping flagged observations is pending_data_quality, not eligible. "
        "A quality flag is a review indication, not proof the score is erroneous."
    ),
}


def policy_passages(policy_ids: list[str], *, variant: str = "baseline") -> list[dict]:
    """Return isolated documents in original fixture order; never mutate baseline."""
    if variant not in POLICY_PASSAGE_VARIANTS:
        raise ValueError("Unsupported policy passage variant")
    if type(policy_ids) is not list or not all(
        type(policy_id) is str and policy_id in _SCRIPTED_POLICY_DOCS
        for policy_id in policy_ids
    ):
        raise ValueError("Unsupported policy IDs")
    docs = copy.deepcopy([_SCRIPTED_POLICY_DOCS[policy_id] for policy_id in policy_ids])
    if variant == "clarified_v1":
        for doc, policy_id in zip(docs, policy_ids):
            doc["text"] = _CLARIFIED_TEXT[policy_id]
    return docs
