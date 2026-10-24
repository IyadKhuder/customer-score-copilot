from copy import deepcopy
import pytest
from src.narrative_findings import extract_verified_policy_findings, FindingError


def example_audit():
    claim = {"field": "warning_signal_status", "value": "triggered", "account_id": 81000001,
             "as_of_period": "2025-03-01", "market": 1}
    return {
        "status": "passed", "schema": {"status": "passed"}, "issues": [], "limitations": [],
        "narrative": {"coverage_method": "llm_draft_excluded"},
        "policy": {"status": "passed", "checks": [{
            "status": "passed", "claim": claim,
            "audit": {"status": "passed", "checks": [{
                "field": "warning_signal_status", "claimed": "triggered", "expected": "triggered",
                "context_matches": True, "value_matches": True, "passed": True,
            }]},
        }]},
    }


def extract(audit):
    return extract_verified_policy_findings(audit, account_id=81000001,
                                            as_of_period="2025-03-01", market=1)


def test_passed():
    found = extract(example_audit())
    assert len(found) == 1
    assert found[0].field == "warning_signal_status"
    assert found[0].value == "triggered"
    assert found[0].event_period is None
    assert found[0].policy_ids == ()


@pytest.mark.parametrize('damage', [
    lambda a: a.update(status="incomplete"),
    lambda a: a["policy"]["checks"][0]["claim"].update(market=2),
    lambda a: a["policy"]["checks"][0]["audit"]["checks"][0].update(expected="not_triggered"),
    lambda a: a["policy"]["checks"][0]["audit"]["checks"][0].update(context_matches=False),
    lambda a: a["policy"]["checks"][0]["claim"].update(field="unsupported_temporal_rule"),
    lambda a: a.update(limitations=["Unknown market onboarding date"]),
    lambda a: a.update(numeric={"status":"passed", "checks":[{"status":"passed", "context_grounded":True, "value_grounded":False}]}),
])
def test_rejects(damage):
    audit = example_audit()
    damage(audit)
    with pytest.raises(FindingError):
        extract(audit)
