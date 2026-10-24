"""Module 6.4 targeted prompt regression tests; never call a paid provider.

This checks that the policy *unit of measurement* is conveyed unambiguously
only where the LLM must report a decline-event count. It intentionally does
not change validator rules, events, benchmark labels or publication gates.
"""
from __future__ import annotations

import pytest

from src import agent


class CapturedPrompt(Exception):
    """Sentinel that stops before any tool call or real LLM request."""


class CaptureClient:
    def __init__(self):
        self.messages = None
        self.tools = None

    def chat(self, *, messages, tools):
        self.messages = messages
        self.tools = tools
        raise CapturedPrompt


COUNT_SCOPES = (
    "decline_events",
    "score_bin_warning_signal_decline_events",
    "score_bin_warning_signal_no_declines",
    "score_bin_warning_signal_quality_hold",
    "grace_decline_events",
)


@pytest.mark.parametrize("scope", COUNT_SCOPES)
def test_explicit_event_count_unit_in_each_count_scope(scope):
    provider = CaptureClient()
    kwargs = {
        "publication_mode": "claims_first",
        "claim_scope": scope,
        "expected_account_id": 123,
        "expected_period": "2025-04-01",
        "expected_market": 3 if scope == "grace_decline_events" else 1,
        "verbose": False,
    }
    if scope == "grace_decline_events":
        kwargs.update(site_onboarded_period="2024-05-01",
                      last_exception_review_period="2025-04-01")
    with pytest.raises(CapturedPrompt):
        agent.run_agent("What occurred?", provider, **kwargs)
    content = provider.messages[0]["content"]
    assert '"raw_event_count.consecutive_declines"' in content
    assert "QUALIFYING STREAK-ENDING EVENTS" in content
    assert "NOT count individual monthly" in content
    assert "three-calendar-month evaluation window" in content
    assert "THREE consecutive downward transitions" in content
    assert "FOUR-transition threshold" in content
    assert "multiple overlapping events" in content
    assert "Do not equate 'warning_signal_status=triggered'" in content
    assert "requested" in content.lower()
    assert "CF-001" not in content
    assert "expected event count: 1" not in content


@pytest.mark.parametrize("scope", ("score_bin", "warning_signal", "score_bin_warning_signal"))
def test_no_event_count_guidance_when_scope_does_not_claim_event_count(scope):
    provider = CaptureClient()
    with pytest.raises(CapturedPrompt):
        agent.run_agent(
            "Summarize account", provider, publication_mode="claims_first",
            claim_scope=scope, expected_account_id=123,
            expected_period="2025-04-01", expected_market=1, verbose=False,
        )
    assert "QUALIFYING STREAK-ENDING EVENTS" not in provider.messages[0]["content"]


def test_legacy_uses_no_event_count_guidance():
    provider = CaptureClient()
    with pytest.raises(CapturedPrompt):
        agent.run_agent("Summarize account", provider, verbose=False)
    assert "QUALIFYING STREAK-ENDING EVENTS" not in provider.messages[0]["content"]


def test_event_count_not_redefined_in_policy_claim_guard():
    """The verifier remains the sole authority on event counts."""
    from src.policy_claim_guard import _expected
    case = {
        "events": [
            {"criterion": "consecutive_declines", "period": "2025-03-01"},
            {"criterion": "score_drop", "period": "2025-03-01"},
        ]
    }
    assert _expected("raw_event_count.consecutive_declines", case) == 1
    assert _expected("raw_event_count.score_drop", case) == 1
    assert _expected("raw_event_count.bin_drop", case) == 0


def test_overlapping_streak_ends_count_as_separate_events():
    """Regression check on the actual detector, independent of prompt text."""
    from src.temporal_policy import evaluate_threshold_signals
    months = [f"2025-{month:02d}-01" for month in range(1, 7)]
    history = [
        {"period": month, "score": 800 - i * 10, "bin": 6, "market": 1}
        for i, month in enumerate(months)
    ]
    result = evaluate_threshold_signals(history, "2025-06-01")
    assert [e["period"] for e in result["events"]["consecutive_declines"]] == [
        "2025-04-01", "2025-05-01", "2025-06-01",
    ]
