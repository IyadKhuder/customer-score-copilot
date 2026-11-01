# M1.3 step 1: bounded role comparison, scripted offline

Date: 1 November 2026. Base: be40d59.

## What we are learning

A role is a responsibility with a bounded input/output contract. It is not
necessarily a separate model or a separate server. Later, one model can serve
two roles using different prompts. This step uses scripted Python callbacks,
not LLM agents. It tests the orchestration and publication boundaries first.

Official LangGraph terminology distinguishes workflows (fixed paths) from agents
that choose their own tools/process. Our experiment is a fixed role workflow:
https://docs.langchain.com/oss/python/langgraph/workflows-agents
Checked 1 November 2026. Do not describe these scripted results as model accuracy.

## Two paths, one publication authority

| Mode | Execution order |
| --- | --- |
| analysis_only | Analysis role, deterministic verification/composition, result |
| analysis_plus_review | Analysis role, one evidence-review pass, deterministic verification/composition, result |

Both use the existing verify_structured_response in claims_first mode and
compose_verified_score_bin. No replacement validator is introduced. The composer
can still block a package whose individual claims passed (for example, when the
required bin claim is missing). Raw analysis prose is never the public answer.

The reviewer returns findings only. An empty findings list cannot override a
failed numerical check or composition block. Findings request inspection and
hold the result; they do not change verification status. Invalid/unavailable
reviewer output also holds the reviewed path. There is no revision/repair loop,
no claim rewriting and no analyst decision. Any later repair must be reverified.

## Inputs and role contracts

The numeric-only fixture contains a trusted request and up to 12 frozen monthly
observations. The demo uses invented scores with demo-bins-1 mapping. It does not
load the account database, retrieve policy passages or evaluate policy rules.
Both paths receive identical evidence and the same scripted proposal, confirmed
by hashes. Roles receive independent copies; mutation is detected and held.

Analysis returns the existing structured-output shape:

```json
{
  "answer": "Unpublished draft text",
  "score_claims": [],
  "policy_claims": [],
  "limitations": []
}
```

Numeric scope allows up to eight score/bin claims, no policy claims, and at most
16 KiB of serialized proposal. The existing parser checks each claim's fields.
The existing verifier checks grounding; the composer enforces the supported scope.

Reviewer output has one permitted top-level field:

```json
{
  "findings": [
    {
      "code": "missing_claim",
      "metric": "bin",
      "evidence_ids": ["obs-1991-12"]
    }
  ]
}
```

At most six findings; codes are missing_claim, unsupported_claim,
context_mismatch or evidence_gap. Metric is score or bin. References must point
to observed evidence IDs; an empty list is allowed when evidence is missing.
No approve, override, replacement claims or unrestricted prose fields are allowed.
A valid structure does not prove the finding is correct: false alarms are part
of the experiment. Expected-outcome labels are never passed to either role.

One analysis invocation and at most one reviewer invocation per workflow.
Analysis failure skips the reviewer. No retries, autonomous tools or conversation
loop. The generic callback interface does not itself budget provider calls; do
not plug a live adapter into it before adding the existing request-budget and
safe provider-accounting integration.

## Read the scripted comparison correctly

The manifest has 10 cases: valid output, fabricated score, missing bin, wrong
month/market, duplicate claim, unavailable evidence, reviewer false alarm,
reviewer failure and attempted reviewer override. Expected publication labels
are explicit manually written fixture expectations. They are not reviewer input.

| Scripted path | Expected publication outcomes matched | Incorrect publications | Unnecessary holds |
| --- | ---: | ---: | ---: |
| analysis_only | 10/10 | 0 | 0 |
| analysis_plus_review | 7/10 | 0 | 3 |

These outcomes are deliberately constructed. The reviewer misses some errors,
which deterministic checks still catch, and introduces three holds on valid
packages. The existing numeric-only scope already checks completeness tightly,
so this fixture does not demonstrate an additional quality benefit. Nor does it
show that LLM reviewers are worse. It demonstrates how the wiring handles the
scripted cases. Do not choose or market an architecture based on these ratios.

Repetitions rerun the same scripts (default three). They are repeatability and
local timing checks, not independent model samples. The report includes fixture
and relevant code hashes, per-case evidence/proposal hashes, reason codes,
verification/composition status, safe public text and invocation counts.
Unverified proposal text and raw exception messages are excluded from reports.

For 10 cases, two modes and three repetitions: 60 graph executions, 60 analysis
invocations and 30 reviewer invocations. Live provider requests: zero. Token
usage and estimated live cost: null/unavailable. Local median graph.invoke wall
time excludes graph compilation; lazy imports, warmup and machine noise can
still affect it. It does not estimate provider latency or live cost.

## Isolation and next step

This experiment has no persistent checkpointer, approval capability, Slack path,
provider client or account action. M1.2's durable workflow is unchanged. It uses
the same pinned dependencies. Reports are new local JSON files, never overwritten
by the CLI. Use the ignored results folder for generated reports.

M1.3 remains In progress after this step. Next, discuss the observed report and
extend to policy/evidence questions where review may add value. A live role
comparison needs versioned prompts, held-out cases, provider/model identity,
explicit bounded request allowance, repeated-run design and safe telemetry.
Actual LLM output revisions must pass deterministic verification again. The
simpler path remains selectable. No live-quality/cost claim is established here.
