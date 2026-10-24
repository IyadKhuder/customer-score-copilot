"""New fixture-v2 replay through existing real verification/composition/gates."""

import argparse
import importlib
import json
from pathlib import Path
import sys
from time import perf_counter
from types import ModuleType

from benchmark_backend import offline_application, execute_case, sanitize, evaluate_observation, sha256

# A dedicated package alias avoids colliding with the external application's src.
package = ModuleType("synthetic_fixture_package")
package.__path__ = [str(Path(__file__).resolve().parents[1]/"src")]
sys.modules[package.__name__] = package
contract = importlib.import_module("synthetic_fixture_package.synthetic_benchmark")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    benchmark, fixtures = contract.load_contract()
    output = args.output_dir.absolute()
    if output.exists() or output.is_symlink(): parser.error("output directory must be new")
    records = []
    with offline_application(args.source_root) as app:
        for case, fixture in zip(benchmark["cases"], fixtures["fixtures"]):
            started = perf_counter()
            try:
                rows = contract.fixture_rows(fixture)
                kwargs = {k: case["request"][k] for k in
                    ("site_onboarded_period", "last_exception_review_period") if k in case["request"]}
                decision = app.decide(rows, case["request"]["as_of_period"], **kwargs)
                response = execute_case(app, case, fixture, rows, contract.scripted_claims(case, fixture))
                observed = sanitize(response)
                checks, facts = evaluate_observation(case, observed, decision)
                record = {"case_id": case["id"], "scenario_id": case["scenario_id"],
                          "observed": observed, "computed_policy_facts": facts,
                          "checks": checks, "passed": all(checks.values()), "error_type": None}
            except Exception as exc:
                record = {"case_id": case["id"], "scenario_id": case["scenario_id"],
                          "observed": None, "computed_policy_facts": None, "checks": {},
                          "passed": False, "error_type": type(exc).__name__}
            record["elapsed_ms"] = round((perf_counter()-started)*1000, 3)
            records.append(record)
        source_hashes, policy_hashes = app.source_sha256, app.policy_sha256
    report = {"report_schema_version": 2, "runner_version": "synthetic-scripted-2",
              "benchmark_id": benchmark["benchmark_id"], "fixture_set_id": fixtures["fixture_set_id"],
              "benchmark_sha256": contract.fingerprint(benchmark),
              "fixture_sha256": contract.fingerprint(fixtures),
              "mapping_version": benchmark["mapping_version"], "policy_version": "v1.0",
              "execution_mode": "scripted_offline", "provider_requests": 0,
              "retrieval_mode": "fixed baseline passages; schema-only import adapter; no TF-IDF ranking",
              "database_mode": "isolated in-memory fixture histories; SQLite connections disabled",
              "application_source_sha256": source_hashes, "baseline_policy_sha256": policy_hashes,
              "harness_source_sha256": {
                  name: sha256(Path(__file__).resolve().parents[1]/name) for name in (
                      "src/synthetic_foundations.py", "src/synthetic_benchmark.py",
                      "validation/benchmark_backend.py", "validation/run_scripted_benchmark_v2.py")},
              "cases": records, "cases_passed": sum(r["passed"] for r in records), "cases_total": len(records),
              "scope": "New v2 fixture replay; not a live-provider result or a replay of the generated CSV database."}
    # Exclusive creation: preserve all earlier reports, including failed runs.
    output.mkdir(parents=True)
    path = output/"scripted_benchmark_v2.json"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, indent=2, sort_keys=True);stream.write("\n")
    for record in records:
        state = "ERROR" if record["observed"] is None else (
            "PUBLISH" if record["observed"]["publishable"] else "BLOCK")
        print(record["case_id"], state, "PASS" if record["passed"] else "FAIL")
    print(f"{report['cases_passed']}/{len(records)} cases passed; 0 provider requests")
    print("Report:", path)
    print("New fixture-v2 scripted replay; not live-provider accuracy or retrieval ranking.")
    return 0 if report["cases_passed"] == len(records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
