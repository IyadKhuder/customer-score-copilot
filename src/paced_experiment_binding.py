"""6.5.2.4: offline binding integration with the actual paced runner.

The caller supplies a simulated case executor, sleep and clock. No live CLI or
client factory is exposed. All patches are process-global: serial use only.
"""
from collections import Counter
from unittest.mock import patch

from src.bound_experiment import rehearse_bound_experiment, save_bound_rehearsal


def rehearse_paced_experiment(plan, root, client, *, case_executor, sleeper, clock):
    """Use the unchanged 6.4.6 pacing/retry/report path with simulated cases.

    case_executor must be offline. Injected Python is not sandboxed. Do not pass
    live run_live_case here; the resulting metadata is always marked offline.
    """
    if not all(callable(x) for x in (case_executor, sleeper, clock)):
        raise TypeError('Offline case executor, sleeper and clock are required')
    # Lazy load after import: importing this module cannot instantiate a client.
    from src import paced_assisted_benchmark as paced
    attempts = Counter()
    failures = {}

    def tracked_case(case, fixture, adapter, **kwargs):
        cid = case['id']
        attempts[cid] += 1
        try:
            return case_executor(case, fixture, adapter, **kwargs)
        except Exception as exc:
            # Store only counts and exception type, never messages/drafts.
            failures.setdefault(cid, []).append(type(exc).__name__)
            raise

    def runner(adapter, **kwargs):
        with patch.object(paced, 'run_live_case', tracked_case):
            return paced.run_paced_assisted_benchmark(
                adapter, sleeper=sleeper, clock=clock, **kwargs)

    report, metadata = rehearse_bound_experiment(plan, root, client, runner=runner)
    record = metadata['execution_record']
    record['runner_path'] = 'src.paced_assisted_benchmark.run_paced_assisted_benchmark'
    record['case_executor'] = 'caller_supplied_offline_simulation'
    record['actual_retries'] = {
        row['case_id']: max(0, attempts[row['case_id']] - 1) for row in report['cases']}
    record['case_attempt_counts'] = {
        row['case_id']: attempts[row['case_id']] for row in report['cases']}
    record['case_exception_types'] = {
        row['case_id']: failures.get(row['case_id'], []) for row in report['cases']}
    record['retry_observation_scope'] = (
        'Simulated run_live_case invocations only; excludes any SDK internal retries. '
        'Zero attempts means failure before the case executor was reached.')
    return report, metadata
