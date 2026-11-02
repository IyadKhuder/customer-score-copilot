# M1.3 Step 2: small live numeric-role pilot

Date: 1 November 2026. Apply to c567194, phase2/persistent-review-workflow.

## What changes

This adds a separate opt-in runner, three paired evidence fixtures, five fixed
reviewer challenges, and versioned analysis/reviewer prompts. It reuses the
existing role graph, verifier, composer, configured Gemini adapter and request
budget. The Step 1 harness and durable review workflow are unchanged. This is
numeric-only plumbing and reviewer-behavior evaluation. Policy review and a
held-out quality study remain later work. M1.3 stays in progress.

Both roles use the model named in src/config.py at plan time. This patch does
not change models, dependencies or configuration. This first runner supports
only the configured Gemini OpenAI-compatible adapter. Anthropic is not selected.
A configured model name does not establish provider availability. The smoke test
is also a check of schema/parameter acceptance by that configured provider.

## Paired experiment A

Generate one analysis proposal per fixture, validate and save it locally, then
replay the same proposal through both paths. Only the reviewed path calls a
live reviewer. Hashes confirm that evidence and proposal match. Reference labels,
case IDs and expected findings never enter role prompts.

The three fixtures contain: a current month at a bin boundary, a missing target
month, and history with the target month present and boundary values. Required
claims are exact-request score and bin. An independent local oracle compares
actual model claims with those observations, rejects duplicates/context errors,
and treats limitations as requiring a hold. It is intentionally narrow. Results
cannot be generalized to policy reasoning, retrieval or arbitrary narratives.

A failure to produce a valid analysis skips review. No proposal is repaired.
An empty reviewer findings list cannot bypass verification/composition. A
reviewer false alarm, invalid output or request failure adds a separate hold.

## Reviewer experiment B

Five prewritten proposals: valid control, wrong score, missing bin, wrong month,
and claims without evidence. Only the reviewer is live. The analysis graph node
replays the fixed proposal locally. Expected code/metric findings are kept out
of prompts and compared after review. Evidence IDs must also pass the existing
reviewer contract. Exact finding labels are a diagnostic rubric: a plausible
alternative code is reported as unexpected/missed and deserves manual inspection.
Reviewer errors are not equivalent to genuine misses; inspect call status too.

Keep A and B results separate. Fixed challenge detection does not measure live
analysis quality, and the numeric deterministic gate already covers these errors.

## Requests and telemetry

| Suite | Live analysis | Live reviewer | Maximum attempts |
| --- | ---: | ---: | ---: |
| smoke | 1 | 1 | 2 |
| pilot | 3 | 8 | 11 |

Smoke and pilot are separate runs. Running both can consume 13 attempts. Failed
requests count; analysis failure may reduce requests. Allowance is caller-declared,
not queried from provider usage. It must cover the chosen suite before client
construction. RequestBudget enforces zero SDK retries, 60-second timeout and at
least five seconds between request starts. This runner adds max_tokens=2048 at
the counted SDK boundary. There are no tools, continuations, fallbacks or repair
requests. Existing output directories are refused before client access.

The dry run prints a credential-free plan with model/settings and fixture,
prompt and source hashes. Live execution checks that plan before setup and
checks source identity again after setup and execution. Model defaults apply to
temperature and reasoning; this is not a cross-provider settings comparison.

- requests.jsonl records actual SDK attempts and sanitized error types.
- report.json separates paired/challenge outcomes and call records. Setup or
  unexpected execution errors leave status incomplete; caught role failures
  produce completed_with_role_failures. Exit code 2 means review is needed.
- LP-A*.proposal.json contains UNVERIFIED model drafts for local inspection.
  Never treat them as public answers or commit the generated results bundle.
- LP-*.result.json preserves per-case results as the run progresses. Raw
  exceptions, credentials and SDK response objects are not saved.
- Provider usage is recorded only when all three integer usage fields exist.
  Missing usage or any failed request prevents a complete aggregate token total.
  Monetary cost remains null. Response model is provider-reported metadata,
  not independently authenticated provenance.

Calls have case IDs, role, attempt number, status, sanitized error category and
elapsed time. Role elapsed includes pacing, SDK work, normalization and parsing.
Paired analysis_plus_path_elapsed_ms reuses the shared analysis elapsed and adds
that path's graph.invoke time. The latter includes review elapsed where relevant
and excludes graph compilation. Sequential pilot timings are not independent
production latency samples. Baseline graph analysis invocations are local
replays: use budget.requests_started for actual requests.

held_correct_proposals includes all holds on oracle-correct proposals, including
reviewer availability/schema failures. It is not a false-alarm count by itself.
Use reason_codes, call status and actual findings to distinguish causes. Zero
incorrect publications on this tiny numeric suite is not a general safety claim.

## Validation and first execution

Follow APPLY_M1_3_STEP2.md: inspect files, run focused tests, full regression and
the dry plan before any live execution. No new installation is needed in an
already working Step 1 environment. Fake SDK tests spend no requests and use no
credentials. The first live checkpoint is the two-request smoke test; inspect
its accounting, drafts and findings before deciding whether to run all cases.

The OpenAI-compatible chat route follows Google's compatibility documentation:
https://ai.google.dev/gemini-api/docs/openai/ (checked 1 November 2026).
Provider acceptance, live output quality and usage availability remain untested
until the user executes the smoke test with their configured provider.

## Patch preparation validation

The full Linux/Python 3.12 regression passed: 1,627 tests and 169 subtests,
after running the repository's synthetic database initialization as CI does.
Two additional real-SDK/mock-HTTP tests were then added and passed in the final
focused group: 61 tests total, including 23 new live-pilot tests. Every test
in the delivered patch has therefore passed; the full run preceded those two
SDK-only test additions. No live provider requests were made. The smoke dry
plan also completed without credentials. No original archive file was modified.
