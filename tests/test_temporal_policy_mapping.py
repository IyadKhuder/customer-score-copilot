"""Module 5.5.6.2: offline adapter tests against documented audit shape."""
from copy import deepcopy

import pytest

from src.temporal_policy_mapping import (
    TemporalPolicyMapping,
    TemporalPolicyMappingError,
    map_verified_decline_events,
)
from src.narrative_findings import VerifiedFinding

ACCOUNT = 81000001
AS_OF = "2025-04-01"
MARKET = 1


def history():
    pairs = [
        ("2024-12-01", 286), ("2025-01-01", 282),
        ("2025-02-01", 275), ("2025-03-01", 267),
        ("2025-04-01", 271),
    ]
    return [dict(period=p, score=s, bin=6, market=MARKET) for p, s in pairs]


def evidence(rows=None):
    return [{"tool_name": "get_history", "tool_call_id": "h1",
             "arguments": {"account_id": ACCOUNT},
             "result": deepcopy(history() if rows is None else rows)}]


def make_audit(count=1):
    claim = dict(field="raw_event_count.consecutive_declines", value=count,
                 account_id=ACCOUNT, as_of_period=AS_OF, market=MARKET)
    return {
        "status": "passed", "schema": {"status": "passed"},
        "answer": "Untrusted model text: warning register was updated!",
        "issues": [], "limitations": [],
        "narrative": {"coverage_method": "llm_draft_excluded", "status": "not_applicable"},
        "numeric": {"status": "not_applicable", "checks": []},
        "policy": {"status": "passed", "checks": [{
            "claim": claim, "status": "passed", "audit": {
                "status": "passed", "account_id": ACCOUNT,
                "as_of_period": AS_OF, "market": MARKET,
                "scope": "explicit policy claims only (not a full free-text audit)",
                "checks": [{"field": claim["field"], "claimed": count,
                            "expected": count, "context_matches": True,
                            "value_matches": True, "passed": True}],
                "unchecked_claims": [],
                "evidence": {"window_start": "2025-02-01", "window_end": AS_OF,
                             "events": [{"criterion": "consecutive_declines"} for _ in range(count)],
                             "data_quality_flags": [], "policy_sources": ["POL-002"]},
            },
        }]},
    }


def map_it(audit=None, ev=None, **context):
    defaults = dict(account_id=ACCOUNT, as_of_period=AS_OF, market=MARKET)
    defaults.update(context)
    return map_verified_decline_events(make_audit() if audit is None else audit,
                                       evidence() if ev is None else ev, **defaults)


def test_maps_single_march_event_with_four_supporting_periods():
    result = map_it()
    assert isinstance(result, TemporalPolicyMapping)
    assert result.raw_event_count == 1
    assert (result.window_start, result.window_end) == ("2025-02-01", AS_OF)
    (finding,) = result.findings
    assert isinstance(finding, VerifiedFinding)
    assert (finding.kind, finding.field, finding.value) == (
        "temporal", "consecutive_score_declines", 3)
    assert finding.event_period == "2025-03-01"
    assert finding.evidence_periods == (
        "2024-12-01", "2025-01-01", "2025-02-01", "2025-03-01")
    assert finding.policy_ids == ()


def test_untrusted_narrative_does_not_change_mapping():
    data = make_audit()
    baseline = map_it(data)
    data["answer"] = "This is certain default and a credit committee referral."
    assert map_it(data) == baseline


def test_april_recovery_is_not_reported_as_an_april_decline():
    assert [f.event_period for f in map_it().findings] == ["2025-03-01"]


def test_event_outside_window_is_not_reported():
    a = make_audit(0)
    a["policy"]["checks"][0]["audit"]["evidence"]["window_start"] = "2025-04-01"
    assert map_it(a).findings == ()


def test_long_streak_emits_two_events_when_policy_count_matches():
    rows = history()
    rows[-1]["score"] = 260
    result = map_it(make_audit(2), evidence(rows))
    assert [f.event_period for f in result.findings] == ["2025-03-01", "2025-04-01"]


def test_raw_count_disagreement_blocks_mapping():
    with pytest.raises(TemporalPolicyMappingError, match="disagree"):
        map_it(make_audit(2))


def test_policy_event_list_disagreement_blocks_mapping():
    a = make_audit()
    a["policy"]["checks"][0]["audit"]["evidence"]["events"] = []
    with pytest.raises(TemporalPolicyMappingError, match="event list disagrees"):
        map_it(a)


def test_reported_policy_count_must_be_integer_not_boolean():
    a = make_audit()
    a["policy"]["checks"][0]["claim"]["value"] = True
    with pytest.raises(TemporalPolicyMappingError, match="Unsupported"):
        map_it(a)


def test_wrong_caller_context_blocks_mapping():
    with pytest.raises(TemporalPolicyMappingError, match="mismatched"):
        map_it(account_id=9999)


def test_failed_or_incomplete_audit_blocks_mapping():
    for status in ("failed", "incomplete"):
        a = make_audit()
        a["status"] = status
        with pytest.raises(TemporalPolicyMappingError, match="did not pass"):
            map_it(a)


def test_unchecked_claims_block_mapping():
    a = make_audit()
    a["policy"]["checks"][0]["audit"]["unchecked_claims"] = [{"reason": "unsupported_field"}]
    with pytest.raises(TemporalPolicyMappingError, match="missing or inconsistent"):
        map_it(a)


def test_wrong_inner_field_verdict_blocks_mapping():
    a = make_audit()
    a["policy"]["checks"][0]["audit"]["checks"][0]["field"] = "warning_signal_status"
    with pytest.raises(TemporalPolicyMappingError, match="verdict"):
        map_it(a)


def test_unsupported_field_blocks_mapping():
    a = make_audit()
    a["policy"]["checks"][0]["claim"]["field"] = "warning_signal_status"
    with pytest.raises(TemporalPolicyMappingError, match="Unsupported"):
        map_it(a)


def test_additional_policy_claim_blocks_instead_of_silent_omission():
    a = make_audit()
    a["policy"]["checks"].append(deepcopy(a["policy"]["checks"][0]))
    with pytest.raises(TemporalPolicyMappingError, match="Exactly one"):
        map_it(a)


def test_limitations_and_issues_block_mapping():
    for key, values in (("limitations", ["register unavailable"]), ("issues", ["unresolved"])):
        a = make_audit()
        a[key] = values
        with pytest.raises(TemporalPolicyMappingError, match="clean claim-first"):
            map_it(a)


def test_wrong_narrative_mode_blocks_mapping():
    a = make_audit()
    a["narrative"]["coverage_method"] = "legacy_scan"
    with pytest.raises(TemporalPolicyMappingError, match="clean claim-first"):
        map_it(a)


def test_additional_april_decline_must_match_policy_count():
    a = make_audit()
    ev = evidence()
    ev[0]["result"][-1]["score"] = 260
    # Score 260 in April creates a *new* streak event and must therefore
    # cause a count mismatch, not silently change the earlier finding.
    with pytest.raises(TemporalPolicyMappingError, match="disagree"):
        map_it(a, ev)


def test_missing_history_is_not_evidence_of_zero_events():
    with pytest.raises(TemporalPolicyMappingError, match="insufficient"):
        map_it(make_audit(0), [])


def test_window_after_reporting_date_is_rejected():
    a = make_audit()
    a["policy"]["checks"][0]["audit"]["evidence"]["window_end"] = "2025-05-01"
    with pytest.raises(TemporalPolicyMappingError, match="window"):
        map_it(a)


def test_malformed_policy_events_rejected():
    a = make_audit()
    a["policy"]["checks"][0]["audit"]["evidence"]["events"] = [{"foo": "bar"}]
    with pytest.raises(TemporalPolicyMappingError, match="malformed"):
        map_it(a)


def test_numerical_checks_can_coexist_but_must_be_grounded():
    a = make_audit()
    a["numeric"] = {"status": "passed", "checks": [
        {"status": "passed", "value_grounded": True, "context_grounded": True,
         "claim": {"account_id": ACCOUNT, "period": AS_OF, "market": MARKET,
                   "metric": "score", "value": 271}}
    ]}
    assert len(map_it(a).findings) == 1
    a["numeric"]["checks"][0]["value_grounded"] = False
    with pytest.raises(TemporalPolicyMappingError, match="Numerical checks"):
        map_it(a)


def test_numeric_claim_for_other_account_is_not_accepted():
    a = make_audit()
    a["numeric"] = {"status": "passed", "checks": [
        {"status": "passed", "value_grounded": True, "context_grounded": True,
         "claim": {"account_id": ACCOUNT + 1, "period": AS_OF, "market": MARKET,
                   "metric": "score", "value": 271}}
    ]}
    with pytest.raises(TemporalPolicyMappingError, match="Numerical checks"):
        map_it(a)


def test_date_must_be_canonical():
    with pytest.raises(TemporalPolicyMappingError, match="ISO"):
        map_it(as_of_period="01.04.2025")


def test_boolean_context_ids_rejected():
    with pytest.raises(TemporalPolicyMappingError, match="integer"):
        map_it(market=True)
