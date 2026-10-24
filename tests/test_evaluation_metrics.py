"""Module 6.3: score benchmark *observations*, not LLM reasoning quality."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from src.evaluation_dataset import load_benchmark
from src.evaluation_metrics import (
    EvaluationScoreError, _phrase_normalize, score_report, validate_run_report,
)
from src.evaluation_runner import load_fixtures, run_benchmark, save_report


def _expected_executor(case, fixture):
    expected = case["expected"]
    published = expected["publishable"]
    answer = "\n".join(expected["public_phrases"]) if published else "NOT FOR PUBLICATION"
    return {
        "answer": answer,
        "status": "passed" if published else "incomplete",
        "publishable": published,
        "next_action": expected["next_action"],
        "reason_codes": list(expected["reason_codes"]),
        "completed": True,
        "tool_rounds": 1,
        "tool_evidence": [
            {"tool_name": "get_history", "result": [{"score": 550, "market": fixture["market"]}]},
            {"tool_name": "retrieve_policy", "result": [
                {"source": f"{p}_example.md", "text": "synthetic"}
                for p in fixture["policy_ids"]
            ]},
        ],
        "raw_model_draft": "UNTRUSTED draft should never reach scores",
    }


def _perfect_report():
    return run_benchmark(_expected_executor, provider_id="test_scripted")


def _scores(report=None):
    return score_report(_perfect_report() if report is None else report)


def _observe(report, index):
    return report["cases"][index]["observed"]


def test_perfect_report_scores_all_six_cases():
    result = _scores()
    assert result["scorer_version"] == "6.3"
    assert [c["case_id"] for c in result["cases"]] == [f"CF-{i:03}" for i in range(1, 7)]
    assert all(c["passed"] for c in result["cases"])
    assert result["metrics"]["cases_total"] == 6
    assert result["metrics"]["cases_passed_all_checks"] == 6
    assert result["metrics"]["all_checks_pass_rate"] == 1.0


def test_expected_negative_case_is_scored_as_success():
    result = _scores()
    negative = result["cases"][3]
    assert negative["case_id"] == "CF-004"
    assert negative["test_type"] == "negative"
    assert negative["expected_publishable"] is False
    assert negative["observed_publishable"] is False
    assert negative["passed"] is True
    assert negative["public_phrase_coverage"] is None
    assert result["metrics"]["correct_blocks"] == 1
    assert result["metrics"]["negative_test_block_recall"] == 1.0
    assert result["metrics"]["negative_test_false_publication_rate"] == 0.0


def test_positive_cases_are_five_and_expected_block_one():
    counts = _scores()["metrics"]
    assert counts["expected_publish_cases"] == 5
    assert counts["correct_publishes"] == 5
    assert counts["expected_block_cases"] == 1
    assert counts["false_blocks"] == 0
    assert counts["false_publishes"] == 0
    assert counts["positive_case_publish_recall"] == 1.0


def test_all_metric_rates_one_for_reference_transcript():
    counts = _scores()["metrics"]
    for name in (
        "publication_decision_accuracy", "next_action_accuracy",
        "reason_code_exact_match_rate", "required_policy_retrieval_coverage",
        "public_phrase_coverage", "all_checks_pass_rate",
    ):
        assert counts[name] == 1.0


def test_expected_policy_coverage_counts_all_memos():
    report = _perfect_report()
    scored = _scores(report)
    assert len(_observe(report, 5)["retrieved_policy_ids"]) == 3
    assert scored["cases"][5]["required_policy_retrieval_coverage"] == 1.0
    assert scored["cases"][5]["missing_required_policy_ids"] == []


def test_missing_policy_id_reduces_coverage_not_publication_match():
    report = _perfect_report()
    _observe(report, 5)["retrieved_policy_ids"].remove("POL-005")
    scored = _scores(report)
    row = scored["cases"][5]
    assert row["publication_match"] is True
    assert row["missing_required_policy_ids"] == ["POL-005"]
    assert row["required_policy_retrieval_coverage"] == pytest.approx(2 / 3, abs=1e-6)
    assert row["passed"] is False
    assert scored["metrics"]["required_policy_retrieval_coverage"] < 1.0


def test_unexpected_retrieved_policy_not_penalized():
    report = _perfect_report()
    _observe(report, 0)["retrieved_policy_ids"].append("POL-006")
    assert _scores(report)["cases"][0]["passed"] is True


def test_blocked_case_still_requires_policy_retrieval():
    report = _perfect_report()
    _observe(report, 3)["retrieved_policy_ids"] = []
    row = _scores(report)["cases"][3]
    assert row["publication_match"] is True
    assert row["reason_codes_match"] is True
    assert row["passed"] is False
    assert row["missing_required_policy_ids"] == ["POL-002"]


def test_wrong_publication_decision_is_counted_as_false_block():
    report = _perfect_report()
    obs = _observe(report, 0)
    obs.update({"publishable": False, "status": "incomplete", "next_action": "gather_evidence",
                "public_answer": "", "reason_codes": ["SOME_BLOCK"]})
    scored = _scores(report)
    assert scored["cases"][0]["publication_match"] is False
    assert scored["metrics"]["false_blocks"] == 1
    assert scored["metrics"]["publication_decision_accuracy"] == pytest.approx(5 / 6, abs=1e-6)
    assert scored["metrics"]["public_phrase_coverage"] < 1.0


def test_false_publication_on_negative_case_is_safety_error():
    report = _perfect_report()
    obs = _observe(report, 3)
    obs.update({"publishable": True, "status": "passed", "next_action": "publish",
                "reason_codes": [], "public_answer": "Unverified clearance"})
    scored = _scores(report)
    row = scored["cases"][3]
    assert row["publication_match"] is False
    assert row["passed"] is False
    assert scored["metrics"]["false_publishes"] == 1
    assert scored["metrics"]["negative_test_block_recall"] == 0.0
    assert scored["metrics"]["negative_test_false_publication_rate"] == 1.0


def test_correct_block_with_wrong_next_action_is_not_pass():
    report = _perfect_report()
    _observe(report, 3)["next_action"] = "reject_permanently"
    scored = _scores(report)
    assert scored["cases"][3]["publication_match"] is True
    assert scored["cases"][3]["action_match"] is False
    assert scored["cases"][3]["passed"] is False
    assert scored["metrics"]["negative_test_block_recall"] == 1.0


def test_correct_block_with_wrong_reason_code_is_not_pass():
    report = _perfect_report()
    _observe(report, 3)["reason_codes"] = ["UNRELATED_ERROR"]
    row = _scores(report)["cases"][3]
    assert row["publication_match"] is True
    assert row["reason_codes_match"] is False
    assert row["passed"] is False


def test_reason_code_order_is_not_significant():
    benchmark = copy.deepcopy(load_benchmark())
    benchmark["cases"][3]["expected"]["reason_codes"] = ["A", "B"]
    fixtures = load_fixtures(benchmark=benchmark)
    run = run_benchmark(_expected_executor, benchmark=benchmark, fixtures=fixtures)
    _observe(run, 3)["reason_codes"] = ["B", "A"]
    scored = score_report(run, benchmark=benchmark, fixtures=fixtures)
    assert scored["cases"][3]["reason_codes_match"] is True


def test_missing_phrase_is_not_publication_failure_but_is_case_failure():
    report = _perfect_report()
    _observe(report, 0)["public_answer"] = "Policy signal is verified"
    row = _scores(report)["cases"][0]
    assert row["publication_match"] is True
    assert row["missing_public_phrases"] == ["eligible for early-warning consideration"]
    assert row["public_phrase_coverage"] == 0.0
    assert row["passed"] is False


@pytest.mark.parametrize("text", [
    "ELIGIBLE FOR EARLY-WARNING CONSIDERATION",
    "eligible  for   early-warning\nconsideration",
    "ELIGIBLE for Early-Warning Consideration",
])
def test_phrase_matching_case_and_space_insensitive(text):
    report = _perfect_report()
    _observe(report, 0)["public_answer"] = text
    assert _scores(report)["cases"][0]["public_phrase_coverage"] == 1.0


def test_phrase_matching_requires_literal_words_not_semantic_paraphrase():
    report = _perfect_report()
    _observe(report, 0)["public_answer"] = "meets the applicable warning threshold"
    assert _scores(report)["cases"][0]["public_phrase_coverage"] == 0.0


def test_phrase_normalization_only_collapses_whitespace_case():
    assert _phrase_normalize("  HELLO\nWorld  ") == "hello world"
    assert _phrase_normalize("Four-month") != _phrase_normalize("four month")


def test_runner_execution_error_gets_no_credit_for_expected_block():
    report = _perfect_report()
    report["cases"][3]["observed"] = None
    report["cases"][3]["error_type"] = "RuntimeError"
    scored = _scores(report)
    assert scored["metrics"]["execution_error_count"] == 1
    assert scored["metrics"]["correct_blocks"] == 0
    assert scored["metrics"]["negative_test_block_recall"] == 0.0
    assert scored["cases"][3]["observed_publishable"] is None
    assert scored["cases"][3]["passed"] is False


def test_runner_error_in_positive_case_is_not_false_block():
    report = _perfect_report()
    report["cases"][1]["observed"] = None
    report["cases"][1]["error_type"] = "ValueError"
    counts = _scores(report)["metrics"]
    assert counts["execution_error_count"] == 1
    assert counts["false_blocks"] == 0
    assert counts["correct_publishes"] == 4
    assert counts["positive_case_publish_recall"] == 0.8


def test_error_typenames_not_messages_and_no_unverified_answer_output():
    report = _perfect_report()
    _observe(report, 0)["public_answer"] = "expected eligible for early-warning consideration SECRET"
    scored = _scores(report)
    assert "SECRET" not in json.dumps(scored)
    assert "raw_model_draft" not in json.dumps(scored)
    assert "fixture" not in scored["cases"][0]


def test_execution_error_only_six_cases_means_no_latency_estimate():
    report = run_benchmark(lambda c, f: (_ for _ in ()).throw(RuntimeError("sensitive")))
    counts = _scores(report)["metrics"]
    assert counts["execution_success_count"] == 0
    assert counts["execution_error_count"] == 6
    assert counts["mean_successful_run_elapsed_ms"] is None
    assert counts["p95_successful_run_elapsed_ms"] is None


def test_known_latencies_mean_and_nearest_rank_p95():
    report = _perfect_report()
    for idx, row in enumerate(report["cases"]):
        row["elapsed_ms"] = float((idx + 1) * 100)
    counts = _scores(report)["metrics"]
    assert counts["mean_successful_run_elapsed_ms"] == 350.0
    assert counts["p95_successful_run_elapsed_ms"] == 600.0


def test_latency_excludes_executor_errors():
    report = _perfect_report()
    for i, row in enumerate(report["cases"]):
        row["elapsed_ms"] = float(100 + i * 50)
    report["cases"][5]["observed"] = None
    report["cases"][5]["error_type"] = "ValueError"
    counts = _scores(report)["metrics"]
    assert counts["mean_successful_run_elapsed_ms"] == 200.0
    assert counts["p95_successful_run_elapsed_ms"] == 300.0


def test_scored_results_have_no_raw_case_questions_history_or_answers():
    serialized = json.dumps(_scores())
    assert "historical score" not in serialized
    assert "score\": 550" not in serialized
    assert "public_answer" not in serialized
    assert "UNTRUSTED" not in serialized


def test_scoring_does_not_mutate_runner_report_or_benchmark():
    report = _perfect_report()
    before = copy.deepcopy(report)
    benchmark = load_benchmark()
    bench_before = copy.deepcopy(benchmark)
    _ = score_report(report, benchmark=benchmark)
    assert report == before
    assert benchmark == bench_before


def test_scored_json_is_deterministic_except_timing_input():
    report = _perfect_report()
    assert _scores(report) == _scores(copy.deepcopy(report))


@pytest.mark.parametrize("change", [
    lambda r: r.update({"benchmark_id": "other"}),
    lambda r: r.update({"benchmark_schema_version": 99}),
    lambda r: r.update({"benchmark_schema_version": True}),
    lambda r: r.update({"benchmark_sha256": "0" * 64}),
    lambda r: r.update({"fixture_sha256": "0" * 64}),
    lambda r: r.update({"fixture_set_id": "wrong"}),
    lambda r: r.update({"runner_version": "future"}),
    lambda r: r.update({"execution_mode": "unknown"}),
    lambda r: r.update({"provider_id": "../secrets"}),
    lambda r: r.update({"extra": "field"}),
    lambda r: r.pop("fixture_sha256"),
])
def test_bad_report_metadata_fails_closed(change):
    report = _perfect_report()
    change(report)
    with pytest.raises(EvaluationScoreError):
        _scores(report)


@pytest.mark.parametrize("change", [
    lambda r: r["cases"].pop(),
    lambda r: r["cases"].append(copy.deepcopy(r["cases"][0])),
    lambda r: r["cases"].reverse(),
    lambda r: r["cases"][0].update({"case_id": "CF-999"}),
    lambda r: r["cases"][1].update({"scenario_id": "wrong"}),
    lambda r: r["cases"][0].update({"extra": "unknown"}),
])
def test_missing_duplicate_or_reordered_cases_refused(change):
    report = _perfect_report()
    change(report)
    with pytest.raises(EvaluationScoreError):
        _scores(report)


@pytest.mark.parametrize("value", [-0.01, True, "10", float("nan"), float("inf"), None])
def test_bad_elapsed_ms_fails_closed(value):
    report = _perfect_report()
    report["cases"][0]["elapsed_ms"] = value
    with pytest.raises(EvaluationScoreError, match="latency"):
        _scores(report)


@pytest.mark.parametrize("change", [
    lambda o: o.update({"publishable": 1}),
    lambda o: o.update({"reason_codes": "BLOCK"}),
    lambda o: o.update({"tool_names": [12]}),
    lambda o: o.update({"retrieved_policy_ids": ["not_policy"]}),
    lambda o: o.update({"retrieved_policy_ids": ["POL-002", "POL-002"]}),
    lambda o: o.update({"tool_rounds": True}),
    lambda o: o.update({"public_answer": None}),
    lambda o: o.update({"extra": "draft"}),
    lambda o: o.pop("status"),
    lambda o: o.update({"status": "made_up"}),
])
def test_malformed_observation_is_rejected(change):
    report = _perfect_report()
    change(_observe(report, 0))
    with pytest.raises(EvaluationScoreError):
        _scores(report)


def test_publishable_observation_cannot_have_nonpublish_action():
    report = _perfect_report()
    _observe(report, 0)["next_action"] = "gather_evidence"
    with pytest.raises(EvaluationScoreError):
        _scores(report)


def test_blocked_observation_cannot_carry_public_answer():
    report = _perfect_report()
    _observe(report, 3)["public_answer"] = "private draft leaked"
    with pytest.raises(EvaluationScoreError):
        _scores(report)


def test_error_and_observation_cannot_coexist():
    report = _perfect_report()
    report["cases"][0]["error_type"] = "ValueError"
    with pytest.raises(EvaluationScoreError):
        _scores(report)


def test_observation_none_requires_typed_error():
    report = _perfect_report()
    report["cases"][0]["observed"] = None
    with pytest.raises(EvaluationScoreError):
        _scores(report)


def test_error_messages_cannot_be_recorded_as_error_type():
    report = _perfect_report()
    report["cases"][0]["observed"] = None
    report["cases"][0]["error_type"] = "Invalid key abc=secret"
    with pytest.raises(EvaluationScoreError):
        _scores(report)


def test_malformed_benchmark_input_refused():
    report = _perfect_report()
    benchmark = copy.deepcopy(load_benchmark())
    benchmark["cases"][3]["expected"]["publishable"] = "no"
    with pytest.raises(ValueError):
        score_report(report, benchmark=benchmark)


def test_changed_benchmark_cannot_score_original_report():
    report = _perfect_report()
    benchmark = copy.deepcopy(load_benchmark())
    benchmark["cases"][0]["title"] += " changed"
    with pytest.raises(EvaluationScoreError, match="fingerprint"):
        score_report(report, benchmark=benchmark)


def test_changed_fixture_cannot_score_original_report():
    report = _perfect_report()
    fixtures = copy.deepcopy(load_fixtures())
    fixtures["fixtures"][0]["scores"][0] += 1
    with pytest.raises(EvaluationScoreError, match="fingerprint"):
        score_report(report, fixtures=fixtures)


def test_score_output_roundtrip_and_no_overwrite(tmp_path):
    scores = _scores()
    destination = save_report(scores, tmp_path / "scored" / "metrics.json")
    assert json.loads(destination.read_text(encoding="utf-8")) == scores
    with pytest.raises(FileExistsError):
        save_report(scores, destination)


def test_cli_scores_saved_report_and_writes_new_file(tmp_path, monkeypatch, capsys):
    from scripts.score_benchmark_63 import main
    source = save_report(_perfect_report(), tmp_path / "run.json")
    output = tmp_path / "scored.json"
    monkeypatch.setattr("sys.argv", ["score_benchmark_63", "--input", str(source),
                                      "--output", str(output)])
    assert main() == 0
    text = capsys.readouterr().out
    assert "Scored 6 benchmark cases" in text
    assert "scripted fixture replay" in text
    assert "CF-004 (negative): PASS" in text
    assert json.loads(output.read_text(encoding="utf-8"))["metrics"]["correct_blocks"] == 1


def test_cli_returns_nonzero_if_expected_case_fails(tmp_path, monkeypatch, capsys):
    from scripts.score_benchmark_63 import main
    report = _perfect_report()
    _observe(report, 0)["public_answer"] = "other text"
    source = save_report(report, tmp_path / "run.json")
    monkeypatch.setattr("sys.argv", ["score_benchmark_63", "--input", str(source),
                                      "--output", str(tmp_path / "scored.json")])
    assert main() == 1
    assert "CF-001 (positive): FAIL" in capsys.readouterr().out


def test_cli_does_not_overwrite_previous_score(tmp_path, monkeypatch):
    from scripts.score_benchmark_63 import main
    source = save_report(_perfect_report(), tmp_path / "run.json")
    out = tmp_path / "scored.json"
    out.write_text("existing", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["score_benchmark_63", "--input", str(source),
                                      "--output", str(out)])
    with pytest.raises(FileExistsError):
        main()
    assert out.read_text(encoding="utf-8") == "existing"


def test_cli_rejects_incompatible_saved_report(tmp_path, monkeypatch):
    from scripts.score_benchmark_63 import main
    report = _perfect_report()
    report["benchmark_sha256"] = "different"
    source = save_report(report, tmp_path / "run.json")
    output = tmp_path / "output.json"
    monkeypatch.setattr("sys.argv", ["score_benchmark_63", "--input", str(source),
                                      "--output", str(output)])
    with pytest.raises(EvaluationScoreError):
        main()
    assert not output.exists()
