"""Module 6.4.5.2: quality-hold limitations contract (no live API calls).

These prompt tests complement, rather than replace, the strict negative
publication tests in test_quality_hold_narrative.py and
 test_insufficient_limitation_classification_645.py.
"""
from __future__ import annotations

import pytest
from src import agent

ACCOUNT = 81000001
PERIOD = "2025-04-01"
MARKET = 1
SCOPE = "score_bin_warning_signal_quality_hold"


class PromptCaptured(Exception):
    pass


class CapturingProvider:
    def __init__(self):
        self.prompt = None

    def chat(self, *, messages, tools):
        self.prompt = messages[0]["content"]
        raise PromptCaptured()


def capture(scope=SCOPE, *, legacy=False):
    model = CapturingProvider()
    args = {"publication_mode": "legacy" if legacy else "claims_first", "verbose": False}
    if not legacy:
        args.update(claim_scope=scope, expected_account_id=ACCOUNT,
                    expected_period=PERIOD,
                    expected_market=3 if scope == "grace_decline_events" else MARKET)
        if scope == "grace_decline_events":
            args.update(site_onboarded_period="2024-05-01",
                        last_exception_review_period="2025-04-01")
    with pytest.raises(PromptCaptured):
        agent.run_agent("Describe the requested account's policy status", model, **args)
    assert isinstance(model.prompt, str)
    return model.prompt


def test_quality_hold_scope_appends_specific_limitation_contract():
    prompt = capture()
    assert "LIMITATION SEMANTICS FOR THE DATA-QUALITY STATUS REPORT" in prompt
    assert "DATA-QUALITY STATUS report" in prompt


def test_pending_status_is_not_treated_as_missing_evidence_automatically():
    prompt = capture()
    assert "A review still being pending is the SUBJECT of this report" in prompt


def test_empty_limitations_are_conditional_not_mandatory():
    prompt = capture()
    assert 'set "limitations": []' in prompt
    assert "genuinely" in prompt
    assert "Do NOT set an empty list to force a PUBLISH decision" in prompt


def test_insufficient_evidence_still_requires_true_limitation():
    prompt = capture()
    assert '"insufficient_evidence" is DIFFERENT' in prompt
    assert 'missing history in "limitations"' in prompt
    assert "application MUST BLOCK publication" in prompt


@pytest.mark.parametrize("snippet", [
    'get_history', 'POL-002/POL-005',
    '"pending_data_quality" status',
    'raw qualifying-event count',
])
def test_pending_publication_condition_depends_on_verifiable_evidence(snippet):
    assert snippet in capture()


@pytest.mark.parametrize("snippet", [
    "unavailable administrative registers",
    "generic caveats about pending review",
    "illustrative",
])
def test_routine_out_of_scope_caveats_are_not_claim_evidence_gaps(snippet):
    assert snippet in capture()


@pytest.mark.parametrize("snippet", [
    "OMIT unsupported claims",
    "describe the actual gap",
    "contradictory",
    "mismatched to the account, market, event dates or reporting",
    "independent verification",
])
def test_negative_test_safeguards_stay_in_prompt(snippet):
    assert snippet in capture()


@pytest.mark.parametrize("other_scope", [
    "score_bin", "warning_signal", "score_bin_warning_signal",
    "decline_events", "score_bin_warning_signal_decline_events",
    "score_bin_warning_signal_no_declines", "grace_decline_events",
])
def test_quality_limitation_contract_does_not_leak_to_other_scopes(other_scope):
    prompt = capture(other_scope)
    assert "LIMITATION SEMANTICS FOR THE DATA-QUALITY STATUS REPORT" not in prompt


def test_legacy_mode_does_not_receive_limitation_contract():
    assert "LIMITATION SEMANTICS FOR THE DATA-QUALITY STATUS REPORT" not in capture(legacy=True)


def test_quality_prompt_keeps_consecutive_event_count_definition():
    prompt = capture()
    assert "QUALIFYING STREAK-ENDING EVENTS" in prompt
    assert "NOT count individual monthly" in prompt


def test_does_not_disclose_benchmark_expected_answers():
    prompt = capture()
    assert "CF-003" not in prompt
    assert "expected outcome: pending_data_quality" not in prompt
    assert "expected event count: 2" not in prompt


def test_quality_prompt_uses_caller_selected_context():
    prompt = capture()
    assert f"account_id={ACCOUNT}" in prompt
    assert f"as_of_period={PERIOD}" in prompt
    assert f"market={MARKET}" in prompt
