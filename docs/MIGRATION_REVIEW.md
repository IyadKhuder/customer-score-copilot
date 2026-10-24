# Integrated candidate migration review

Date: 2026-10-23  
Status: first integrated review candidate; local Windows validation and owner
review precede any fresh Git history or publication.

## Inputs reconciled

The uploaded source archive was reconciled with the owner's path-only inventory
at source commit `3fc0a3b2400e5ef316a01d29b1eed0ecf475c52c`. The inventory reported
193 selected tracked paths, 99 ignored paths and no tracked changes. Its scope
was selected relevant folders/root patterns, not every file on the computer.

The only selected tracked path absent from the source ZIP was the old score
CSV. It was intentionally replaced rather than requested/copied. The 82 ignored
paths absent from the ZIP were historical outputs, cached data, visuals and
scratch/browser assets, including a scratch agent copy. None is required as an
authoritative source for this candidate. No additional file contents were needed.

The policy snapshots and generator supplied separately in the ZIP accounted for
ignored source material that was available for review. The duplicate baseline
snapshot was excluded; the experimental memo bytes were relocated distinctly.
The path inventory did not include root INI patterns; `pytest.ini` was already
present in the source ZIP and retained.

## Included and excluded

Included: reviewed application source, all 56 original test files, five new
test files, independently generated small CSV/sidecars, independently authored
v2 benchmark fixtures, baseline policy memos, distinct experimental v1.1 memos,
dependency constraints, adapted CI, new documentation and validation helpers.

Excluded: old Git history, original/mixed-source data, cached databases,
historical live/scripted reports, raw requests, comparison configuration tied to
those reports, old docs/blueprints, old images/screenshots, environment files,
virtual environments, archives and scratch/browser files. No historical result
was relabeled as a run on replacement data.

The package contains no Git directory or prebuilt database. Setup generates
the database locally. No old repository was deleted, rewritten or published.

## Code changes

- `src.data_loader` retains its existing callable API. Its command entry point
  now verifies the three synthetic inputs by exact regeneration before building
  and replacing the candidate's database. A partial/modified input bundle fails
  before replacement; new tests assert preservation of a previous database.
- Score-tool query logic is unchanged; examples/main output now use new data.
- Agent JSON-format examples and Slack usage help use fictional context.
  The `run_agent` implementation and review-service methods are unchanged.
- Evaluation loaders/runners and live-fixture adapters now consume the distinct
  v2 benchmark identity and per-observation bins. Provider experiment metadata
  follows the new fixture paths. Core scorer/transport/request-control behavior
  is retained.
- The retriever's experimental path points to the tracked v1.1 corpus. Baseline
  selection, tokenizer, retrieval algorithm and process-scoped cache remain.
- The old source-dependent generator is absent. The verified independent
  generator, mapping and database helper are included.

Key files are byte-identical to the supplied source: numerical guardrail,
structured-output parser, verification pipeline, policy-claim guard, policy
decision engine, temporal-policy engine, response gate, request budget, provider
adapter implementation, bounded scanner, optional batch notes and Slack
listener. Other unchanged modules are recorded in the machine-readable file
manifest. Method-AST comparisons additionally confirm unchanged `run_agent`,
query functions and review-service methods where surrounding examples changed.

## Test migration

All inherited test files and their test-definition counts were retained.
Copied score-history literals were replaced by an independently authored,
non-affine trajectory. Shared benchmark vectors now use the new frozen v2
histories. Dependent contexts, expected exact values and invalid-version probes
were updated to the new fixture contract. Tests still reject wrong facts,
context, evidence, claim counts, stale fingerprints and unsupported publication.

The v2 positive event fixture now has a complete baseline, so its coverage
assertion is true. Separate insufficient-history, gap and blocked-clearance
tests remain. Real-data tests now require generated setup; absent inputs or
required validators fail instead of silently skipping them. The platform
symlink-permission skip remains legitimate.

Old single-value score tokens associated with the copied trajectory were
replaced in matching claims and expected prose as well as the literal vectors.
This adapts assertions to independently authored data; the replacement dataset
is never produced by transforming the old histories.

## Audit limits

The private review screen found no remaining nonconstant four-score literal
list/tuple windows matching the supplied market-1 histories. This closes the
specific previously flagged literal-overlap finding; it is not a universal
proof of provenance. New fixtures and default data have explicit authoring and
generator provenance. Controlled low-level threshold/malformed tests remain
distinct from valid generated histories.

Hashes, allowlisted files, code comparisons and passing tests are review
evidence, not blanket publication clearance. Review the candidate contents and
complete the local validation before making the fresh repository public.
