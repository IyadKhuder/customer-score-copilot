"""Module 6.4.6: offline, deterministic positive and negative tests.

No actual provider calls, quota use, sleeps, score database, or credential access.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from src import paced_assisted_benchmark as mod
from src.evaluation_runner import load_fixtures
from src.evaluation_dataset import load_benchmark
from src.evaluation_metrics import score_report, validate_run_report
from scripts import run_paced_assisted_benchmark_646 as cli


class Client:
    def chat(self, **kwargs):
        pass

    def continue_with_tool_results(self, **kwargs):
        pass


class FakeTimer:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def clock(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds

    def work(self, seconds):
        self.t += seconds


def result(case, fixture, *, published=True, private_token="UNTRUSTED_SECRET"):
    docs = [
        {"source": f"{name}_policy.md", "text": private_token}
        for name in fixture["policy_ids"]
    ]
    return {
        "answer": "Trusted public account narrative" if published else private_token,
        "status": "passed" if published else "incomplete",
        "publishable": published,
        "next_action": "publish" if published else "gather_evidence",
        "reason_codes": [] if published else ["POLICY_EVIDENCE_INSUFFICIENT"],
        "completed": True,
        "tool_rounds": 3,
        "tool_evidence": [
            {"tool_name": "get_history", "result": [private_token]},
            {"tool_name": "retrieve_policy", "result": docs},
            {"tool_name": "evaluate_policy_signals", "result": {"note": private_token}},
        ],
    }


@pytest.fixture
def inputs():
    bench = load_benchmark()
    fixtures = load_fixtures(benchmark=bench)
    return bench, fixtures


def run_fake(monkeypatch, *, timer=None, raised=None, kwargs=None):
    timer = timer or FakeTimer()
    calls = []
    failures = raised or {}

    def fake_live(case, fixture, provider, **config):
        assert provider is CLIENT
        assert config["policy_tool_suite"] is True
        assert config["policy_passage_variant"] == "clarified_v1"
        calls.append(case["id"])
        timer.work(0.2)
        current = failures.get(case["id"])
        if current:
            if isinstance(current, list):
                error = current.pop(0)
                if error is not None:
                    raise error
            else:
                raise current
        return result(case, fixture, published=case["id"] != "CF-004")

    monkeypatch.setattr(mod, "run_live_case", fake_live)
    report = mod.run_paced_assisted_benchmark(
        CLIENT, provider_id="gemini.flash.assisted_paced_v1",
        sleeper=timer.sleep, clock=timer.clock,
        **(kwargs or {}),
    )
    return report, timer, calls


CLIENT = Client()


class RateLimitError(Exception):
    pass


class GenericStatusError(Exception):
    def __init__(self, status_code, msg):
        super().__init__(msg)
        self.status_code = status_code


@pytest.mark.parametrize("exc,expected", [
    (RateLimitError("secret details"), True),
    (GenericStatusError(429, "unknown payload"), True),
    (GenericStatusError(400, "bad request"), False),
    (ValueError("429 in the text, but not an HTTP status"), False),
    (RuntimeError("Other exception"), False),
])
def test_rate_detection_exact(monkeypatch, exc, expected):
    assert mod.is_rate_limit_error(exc) is expected


def test_serial_order_and_cooldown_excluded_from_latency(monkeypatch, inputs):
    report, timer, calls = run_fake(monkeypatch)
    assert calls == ["CF-001", "CF-002", "CF-003", "CF-004", "CF-005", "CF-006"]
    assert timer.sleeps == [65.0] * 5
    assert [x["elapsed_ms"] for x in report["cases"]] == [200.0] * 6
    assert report["execution_mode"] == "provider_adapter"
    assert report["provider_id"].endswith("paced_v1")
    assert len(report["cases"]) == 6
    assert report["cases"][3]["observed"]["next_action"] == "gather_evidence"
    validate_run_report(report, *inputs)
    assert score_report(report, benchmark=inputs[0], fixtures=inputs[1])["metrics"]["execution_error_count"] == 0


def test_first_case_starts_immediately_and_last_has_no_pause(monkeypatch):
    _, timer, calls = run_fake(monkeypatch, kwargs={"between_case_seconds": 9})
    assert len(calls) == 6
    assert timer.sleeps == [9.0] * 5
    assert round(timer.clock(), 1) == 46.2


def test_retry_one_case_on_rate_limit(monkeypatch):
    report, timer, calls = run_fake(monkeypatch, raised={"CF-003": [RateLimitError("don't save me"), None]})
    assert calls.count("CF-003") == 2
    assert timer.sleeps.count(65.0) == 6  # five inter-case + one backoff
    assert report["cases"][2]["elapsed_ms"] == 65400.0
    assert report["cases"][2]["observed"]["publishable"] is True
    assert "don't save me" not in json.dumps(report)


def test_exponential_backoff_two_retries(monkeypatch):
    report, timer, calls = run_fake(
        monkeypatch,
        raised={"CF-003": [RateLimitError("429"), RateLimitError("429"), None]},
        kwargs={"max_rate_limit_retries": 2, "retry_delay_seconds": 10,
                "between_case_seconds": 1},
    )
    assert calls.count("CF-003") == 3
    assert timer.sleeps == [1.0, 1.0, 10.0, 20.0, 1.0, 1.0, 1.0]
    assert report["cases"][2]["elapsed_ms"] == 30600.0


def test_exhausted_rate_limit_recorded_as_error(monkeypatch, inputs):
    report, timer, calls = run_fake(
        monkeypatch, raised={"CF-006": RateLimitError("my API key secret")},
        kwargs={"max_rate_limit_retries": 1},
    )
    assert calls.count("CF-006") == 2
    assert report["cases"][-1]["observed"] is None
    assert report["cases"][-1]["error_type"] == "RateLimitError"
    assert "my API key secret" not in json.dumps(report)
    validate_run_report(report, *inputs)
    scored = score_report(report, benchmark=inputs[0], fixtures=inputs[1])
    assert scored["metrics"]["execution_error_count"] == 1
    assert scored["cases"][-1]["passed"] is False


def test_cooldown_error_keeps_report_scoreable(monkeypatch, inputs):
    timer = FakeTimer()

    def fail_after_first_case(seconds):
        raise RuntimeError("private cooldown diagnostic")

    monkeypatch.setattr(mod, "run_live_case", lambda case, fixture, client, **config:
                        (timer.work(0.1), result(case, fixture))[1])
    report = mod.run_paced_assisted_benchmark(
        CLIENT, provider_id="gemini.assisted_paced_v1",
        sleeper=fail_after_first_case, clock=timer.clock,
    )
    assert report["cases"][0]["observed"] is not None
    assert all(row["error_type"] == "RuntimeError" for row in report["cases"][1:])
    assert all(row["elapsed_ms"] == 0 for row in report["cases"][1:])
    assert "private cooldown diagnostic" not in json.dumps(report)
    validate_run_report(report, *inputs)


def test_nonrate_error_is_not_retried(monkeypatch):
    report, _, calls = run_fake(monkeypatch, raised={"CF-002": RuntimeError("private error")})
    assert calls.count("CF-002") == 1
    assert report["cases"][1]["error_type"] == "RuntimeError"
    assert "private error" not in json.dumps(report)


def test_real_http429_generic_error_retries(monkeypatch):
    report, _, calls = run_fake(monkeypatch, raised={"CF-002": [GenericStatusError(429,"private"), None]})
    assert calls.count("CF-002") == 2
    assert report["cases"][1]["error_type"] is None


def test_non429_error_is_not_retried(monkeypatch):
    report, _, calls = run_fake(monkeypatch, raised={"CF-002": GenericStatusError(403,"private")})
    assert calls.count("CF-002") == 1
    assert report["cases"][1]["error_type"] == "GenericStatusError"


@pytest.mark.parametrize("options,error", [
    ({"provider_id": "bad label"}, ValueError),
    ({"provider_id": "a"*65}, ValueError),
    ({"policy_passage_variant": "unknown"}, ValueError),
    ({"max_tool_rounds": True}, ValueError),
    ({"max_tool_rounds": 9}, ValueError),
    ({"between_case_seconds": -1}, ValueError),
    ({"between_case_seconds": 601}, ValueError),
    ({"between_case_seconds": "60"}, ValueError),
    ({"retry_delay_seconds": -2}, ValueError),
    ({"retry_delay_seconds": 601}, ValueError),
    ({"max_rate_limit_retries": True}, ValueError),
    ({"max_rate_limit_retries": 4}, ValueError),
    ({"sleeper": None}, TypeError),
    ({"clock": None}, TypeError),
])
def test_bad_parameters_rejected(options, error):
    base = {"provider_id": "valid", "sleeper": lambda _: None, "clock": lambda: 0.0}
    base.update(options)
    with pytest.raises(error):
        mod.run_paced_assisted_benchmark(CLIENT, **base)


def test_missing_provider_methods_fail():
    with pytest.raises(TypeError):
        mod.run_paced_assisted_benchmark(object(), provider_id="ok")


def test_invalid_benchmark_rejected_before_llm_call(monkeypatch, inputs):
    called = []
    monkeypatch.setattr(mod, "run_live_case", lambda *a, **k: called.append(1))
    bad = copy.deepcopy(inputs[0])
    bad["cases"] = []
    with pytest.raises(ValueError):
        mod.run_paced_assisted_benchmark(CLIENT, provider_id="ok", benchmark=bad,
                                          sleeper=lambda _: None)
    assert not called


@pytest.mark.parametrize("args", [
    ["--provider", "gemini", "--model-label", "gemini-3.5", "--output", "unused.json"],
    ["--provider", "gemini", "--model-label", "!!!", "--output", "unused.json", "--confirm-live-api"],
    ["--provider", "gemini", "--model-label", "gemini", "--output", "unused.json", "--confirm-live-api", "--between-case-seconds", "-1"],
    ["--provider", "gemini", "--model-label", "gemini", "--output", "unused.json", "--confirm-live-api", "--max-rate-limit-retries", "4"],
])
def test_cli_rejects_invalid_input_before_client(monkeypatch, args):
    called = []
    with pytest.raises(SystemExit) as exc:
        cli.main(args, client_factory=lambda _: called.append(1))
    assert exc.value.code == 2
    assert called == []


def test_cli_creates_standard_scoreable_report_without_provider_calls(monkeypatch, tmp_path):
    report, _, _ = run_fake(monkeypatch)
    chosen = tmp_path / "report.json"
    used = []
    def factory(provider):
        used.append(provider)
        return CLIENT
    def runner(client, **kwargs):
        assert client is CLIENT
        assert kwargs["provider_id"] == "gemini.gemini-3.5.assisted_paced_v1"
        assert kwargs["max_rate_limit_retries"] == 1
        assert kwargs["between_case_seconds"] == 65.0
        return copy.deepcopy(report)

    code = cli.main([
        "--provider", "gemini", "--model-label", "gemini-3.5",
        "--output", str(chosen), "--confirm-live-api",
    ], client_factory=factory, benchmark_runner=runner)
    assert code == 0
    assert used == ["gemini"]
    saved = json.loads(chosen.read_text("utf-8"))
    assert len(saved["cases"]) == 6
    assert saved["cases"][3]["observed"]["publishable"] is False
    assert "UNTRUSTED_SECRET" not in chosen.read_text("utf-8")


def test_cli_will_not_overwrite(monkeypatch, tmp_path):
    path = tmp_path / "taken.json"
    path.write_text("{}", encoding="utf-8")
    calls = []
    with pytest.raises(SystemExit) as exc:
        cli.main(["--provider", "gemini", "--model-label", "some-model",
                  "--output", str(path), "--confirm-live-api"],
                 client_factory=lambda _: calls.append(1))
    assert exc.value.code == 2
    assert calls == []
    assert path.read_text("utf-8") == "{}"


def test_cli_saves_errors_and_returns_nonzero(monkeypatch, tmp_path):
    report, _, _ = run_fake(monkeypatch, raised={"CF-006": RateLimitError("secrets")})
    path = tmp_path / "error_report.json"
    code = cli.main(["--provider", "gemini", "--model-label", "gemini-3.5",
                     "--output", str(path), "--confirm-live-api"],
                    client_factory=lambda _: CLIENT,
                    benchmark_runner=lambda client, **kwargs: report)
    assert code == 1
    assert path.exists()
    assert json.loads(path.read_text("utf-8"))["cases"][5]["error_type"] == "RateLimitError"


def test_model_label_length_validated_before_provider_setup(tmp_path):
    p = tmp_path / "out.json"
    calls=[]
    with pytest.raises(SystemExit) as exc:
        cli.main(["--provider", "gemini", "--model-label", "x"*50,
                  "--output", str(p), "--confirm-live-api"],
                 client_factory=lambda _: calls.append(1))
    assert exc.value.code == 2
    assert calls == []
