# M4.1 — operational summary and offline case-review benchmark

14 November 2026. Prepared against `183b96a` on
`phase2/persistent-review-workflow`. Owner application, Windows verification,
commit, push and CI remain acceptance gates. No Azure session is required.

## Purpose and source inventory

The independent case-review application accepts structured synthetic case requests,
saves deterministic analysis and evidence, and pauses for human review. LangGraph
supports the durable workflow; no autonomous LLM investigation is implemented.
The Slack bridge is optional future integration and is not implemented here.

At the checkpoint, `review_workflow.py` already saves creation/update times and
transition events, including safe operational reason codes. `case_application.py`
provides durable idempotent ownership records and a single background worker;
`case_queue.py` filters before pagination. Readiness exposes worker failure.
The frontend authenticates case reads and decisions, and clears private content
on disconnect. Existing scoring infrastructure uses sample counts, elapsed times
and nearest-rank p95, but its scripted/role experiments measure different paths.
`role_comparison_live.py` has optional provider token usage and no monetary cost
calculation. It is not the case-review runtime.

This patch adds `operations.py`, a thin `MeasuredApplication` adapter, authenticated
`GET /operations`, a manually refreshed UI panel, and a separate HTTP/worker
benchmark using existing FastAPI TestClient, SQLite, LangGraph and pytest tooling.
No dependency, deployment, workflow-state or database schema change is introduced.
The six fingerprinted modules and frozen review packages are unchanged. The adapter
calls inherited submission, worker and decision operations without modifying them.
Existing cases therefore retain their implementation match and review eligibility.

## Measurement contract

All activity windows are UTC `[snapshot - 24h, snapshot]`, inclusive. Current state
counts cover every API-owned case at the snapshot, regardless of creation date or
queue pagination. Teaching CLI cases are excluded. Each summary reads the required
fields in one SQLite query; no evidence, receipt key, package hash, reviewer or
analyst-entered reason is returned. Summary generation itself is not timed as case
traffic. Authentication and no-store headers apply to the endpoint.

| Measurement | Definition and population |
| --- | --- |
| New submissions | Distinct durable API-owned cases created in the window. Replays are not new cases. |
| Queue delay | Creation to first running event; included when first running falls in the window. Snapshot capture before creation is excluded. |
| Processing elapsed | First running to first awaiting-review/held/failed event; included when that outcome falls in the window. Wall clock can include interruption/downtime. |
| End-to-end lifecycle | Creation to that first processing outcome, including queue delay and excluding analyst waiting. Same outcome window. |
| Closed analyst waiting | First awaiting-review event to completed decision event, selected by decision time in the window. |
| Open analyst waiting | First awaiting-review event to snapshot for every case currently awaiting review, including older cases. Maximum is oldest known waiting age. |
| Failure rate | Cases whose first processing outcome is failed in the window / all cases reaching a first processing outcome in the window. Awaiting-review and held are in the denominator. No outcomes means unavailable. |
| Verification and operational holds | Counts by allowlisted transition reason. Blocked and incomplete are separate; interruption/implementation holds are not model verification failures. Unknown reason text becomes OTHER. Counts have no implicit rate denominator. |
| Request latency | Middleware entry to response creation, grouped by case-route template, method and HTTP status class. Includes local application work; excludes client/network transmission. Authentication/validation failures reaching known case routes are included. Health, static, unknown and summary routes are excluded. |
| Submission stage | Inherited submit operation: hash/lookup and either replay or snapshot capture plus persistence. Includes replays; not pure snapshot time. |
| Worker-cycle stage | One tick, including lock wait, zero or several cases, graph/store operations and recovery. Idle cycles are included. Not per-case analyzer time. |
| Decision stage | Inherited decide operation, including lock wait, validation and saved decision/checkpoint writes. Errors have a separate outcome group. |

Latency summaries use eligible, measured and missing counts, median, maximum and
nearest-rank p95. Each eligible lifecycle interval needs valid, nondecreasing
endpoints. Missing/invalid timestamps are not coerced to zero. Invalid event time
count is returned. Entirely missing events cannot be reconstructed; corresponding
intervals are unavailable, not evidence that no work occurred. All units are ms.
Small-sample p95 often equals the maximum and is not statistical certainty.
`updated_at` is not used as analysis completion because a later decision updates it.

Request/stage samples use `perf_counter`, are retained in a shared 10,000-observation
ring, filtered to the last 24 hours, and reset on process restart. The report shows
collection start (app factory), requested window, oldest retained sample, sample count and capacity
evictions since start. Idle worker cycles can fill the ring in roughly 42 minutes;
these are explicitly retained-sample counts, not complete 24h traffic totals.
Persisted lifecycle history survives restart; timing samples do not. No timings
are backfilled. This deliberately modest implementation adds no monitoring stack.

Stage durations overlap with HTTP time and each other. Do not sum them. Pure
snapshot, deterministic analyzer CPU time and per-case active processing time are
unavailable; the coarser measured stages and durable elapsed intervals are clearly
labelled. Fine-grained persistent telemetry can be added later if justified.

The provider section states offline mode/live notes disabled. Started provider
calls, token usage, usage coverage and monetary cost are null/unavailable. The
runtime has no provider call collector; no token count, price or cost is invented.
The benchmark likewise performs no provider calls. No paid monitoring is added.

Operational aggregation parses saved operational JSON fields without revalidating
all frozen package hashes. It is an operational view, not an integrity audit.
Malformed stored JSON causes the existing safe API error rather than a partial
success. Its full-store scan is suitable for this small single-worker demo;
large-scale indexing, HA, multiple workers and load guarantees are out of scope.

## Reproducible isolated benchmark

Run from the repository with the existing development environment:

```powershell
.\.venv\Scripts\python.exe -m scripts.benchmark_cases_phase2 --output generated/m41_owner_01.json --repetitions 5
if ($LASTEXITCODE -ne 0) { throw 'Benchmark failed' }
```

Choose a new output name each run. Existing files are refused. The command creates
its own temporary synthetic scores DB and case/checkpoint store, closes the worker,
and removes those temporary databases. It never loads or rebuilds the owner's
selected database, uses an owner API token, calls Azure or calls a provider.
The token embedded in the benchmark is disposable and confined to TestClient.

Two authored scenarios: a four-month decline that reaches awaiting_review/passed,
and an absent account that reaches held/incomplete. One warmup per scenario is
excluded; default five measured repetitions per scenario yield ten samples.
Each request uses a distinct durable key. The real background worker runs at its
normal polling cadence. Reported client elapsed time starts before submission and
ends at readback of a saved package, with a 10ms polling interval plus scheduling
and HTTP read overhead. The 15-second polling deadline is a test guard, not a
production latency target. Unexpected HTTP/status/verification/package outcomes
fail the run; expected holds count as correct behavior.

The report records revision, dirty-state flag, code/policy hashes, Python/platform
and relevant dependency versions. A dirty report names the checkpoint plus exact
measured file hashes; it is not evidence of a committed M4.1 revision. Sequential
in-process HTTP timings exclude public TLS/network, browser, analyst decisions and
Azure. No throughput, concurrency, cost or live-model quality claim is made.
The report is a benchmark of two defined paths, not comprehensive functional
coverage. Existing tests cover decisions, recovery, conflicts and idempotency.

## Local owner verification and acceptance

Apply the consolidated patch using the accompanying `APPLY_AND_VERIFY.md`.
Build the frontend, run focused tests, full offline regression and the isolated
benchmark. With the existing local server restarted against the patched source,
connect normally and click **Refresh operations**. The summary is deliberately
manual: refresh again after processing or deciding a local synthetic case.
Confirm counts, labels, unavailable provider values and disconnect clearing.
Local UI verification uses the existing local setup; do not redeploy or start Azure.
If using Docker locally, rebuild the existing service without deleting its volume.

Acceptance requires owner Windows checks, a fresh owner benchmark, explicit-path
commit/push and green CI. Hosted re-verification is not a gate for this local-only
checkpoint. No project hours are logged by this patch. M4.2 follows for standalone
purpose, README, limitations and a repeatable case-review demo, before repository
extraction. Do not expand that checkpoint into additional product features.

## Assistant verification — 14 November 2026

In isolated Linux/Python 3.12.14 checkouts with the repository's pinned development
requirements, the untouched 183b96a archive passed 1,697 tests plus 169 subtests.
The patched checkout passed 1,701 tests plus 169 subtests. Both used freshly built,
verified synthetic data in scratch checkouts; the first patched run without this
required database had six missing-table failures, resolved by that fixture setup.
No owner database was accessed. A subsequent focused operational run passed all
four new tests after final telemetry-label adjustments. Frontend build and all
20 Node checks passed; `pip check` reported no broken requirements. The expected
Starlette TestClient deprecation warning remains. The isolated default benchmark
passed 10/10 measured expected outcomes after two excluded warmups. Its report
records exact measured source hashes and a dirty checkpoint, not a committed patch.
Windows, local owner UI observations, Docker rebuild and GitHub CI were not run
by the assistant. Existing container/CI coverage remains available to the owner.
