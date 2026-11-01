# M1.2: decisions on saved review packages and restart recovery

Date: 1 November 2026. Base commit: 5b59365.

## Learn the decision contract first

M1.1 stopped at the human-review interrupt. M1.2 lets the operator submit a
structured response through Command(resume=payload). LangGraph restarts the
await_review node and returns the payload from interrupt(). The node validates
and records the response, then reaches END. The analyze node is not rerun.

| Field supplied with a decision | Purpose |
| --- | --- |
| case_id | Which case is being reviewed |
| expected_revision | Which saved case revision the analyst inspected |
| package_hash | Which exact saved analysis, verification and evidence binding |
| decision | approved or rejected |
| reviewer | Identity asserted by the offline operator |
| reason | Required nonblank explanation, at most 1,000 characters |

Validation happens before invoking the graph, so invalid submissions normally
leave its checkpoint unchanged, and again inside the case-writing operation.
Only demo-analyst is allowed by the default demo policy. An unlisted name is
rejected. This is an authorization-rule example, not identity authentication:
a local user can type demo-analyst. The later authenticated API must obtain
identity from its trusted session and supply its permitted reviewer policy.
Do not expose this CLI as a public or multi-user service.

## States and audit

| Before | Action | After |
| --- | --- | --- |
| awaiting_review + passed + pending | Approve exact revision/package | completed + passed + approved |
| awaiting_review + passed + pending | Reject exact revision/package | completed + passed + rejected |
| queued / held / failed | Submit decision | Refused, no decision |
| blocked / incomplete verification | Submit decision | Refused, no override |
| already decided | Duplicate or conflicting submission | Refused, original preserved |
| stale revision / mismatched package / reviewer not permitted | Submit decision | Refused |

Both decisions finish review of this exact package. Rejection does not mean that
verification failed; it records the analyst's disagreement or refusal to accept
the package. Neither decision makes a lending decision, registers/clears a warning,
changes an account or publishes externally. Revised evidence requires a new case.

The existing three statuses remain independent. A completed example has revision
3: queued -> running -> awaiting_review -> completed. The saved decision adds
reviewer, reason, UTC decision time, reviewed_revision (2), snapshot_hash and
package_hash. analysis and package_hash remain unchanged. A decision_hash detects
accidental alteration of the audit record. These hashes are not signatures or
protection against an operator who can rewrite the database and its hashes.

The status, decision and audit event are written in one SQLite transaction with
an expected-revision comparison. If two callers read revision 2, only one can
commit revision 3. This protects case decisions; parallel graph workers remain
unsupported. Successful duplicate HTTP semantics/idempotency keys are later work.

The JSON case schema retains version 1 with additive decision/decision_hash fields.
Old pending cases without these fields can still be read by CaseStore. The graph
contract changes to offline-review-graph-v2. Use a new graph demo database for
this step. Old v1 graph checkpoints are refused; no silent migration is performed.
Retain older files if you want to inspect them with their original code version.

## Recovery behavior

| Persisted condition at restart | resume behavior |
| --- | --- |
| queued case, analyze next | Run the bounded offline analysis |
| analysis committed, graph checkpoint behind | Reuse saved result; no recomputation |
| running, no committed result | Move to held with INTERRUPTED_ANALYSIS_NO_RESULT |
| awaiting_review, no decision | Return saved package; keep waiting |
| decision committed, graph still paused | Use the saved decision to finish graph only |
| completed case and completed graph | Return unchanged result |
| held or failed and graph ended | Return unchanged result; no automatic retry |

The case write and graph checkpoint are separate commits. If a crash occurs
between them during a decision, show may report completion_recovery_needed=true.
resume completes the graph without another decision or timestamp/revision change.
This recovery does not accept a new analyst instruction. The durable validated
decision is the authority. Submitting decide again is rejected, including when
recovery is needed; use resume instead.

A held interrupted analysis is not automatically retried. Inspect its evidence
and diagnostics, then explicitly create a new offline demo case if appropriate.
Do not edit database rows to clear a hold. Live-provider uncertain outcomes and
provider-attempt budgets are not implemented and must not be replayed via this
example. A crash between case creation and initial checkpoint can still leave an
orphan case; automated repair is not included.

## Verification holds and safe output

Blocked/incomplete analysis retains frozen evidence and safe diagnostic codes.
Its saved analysis has claims=[], answer=null, and reason_codes. Unverified draft
text is not saved. ANALYSIS_EXECUTION_FAILED records a failure without a raw
exception string. A blocked note does not remove frozen evidence. This remains
the toy score-difference analysis, not integration with the full scan/note pipeline.
Only structured, explicitly passed analysis may reach human review.

## Boundaries and validation

No dependency changes or live model calls. No Slack changes, notification,
account action, production authentication, multi-worker execution or merge.
SQLite files and their checkpoint audit content are private local demo artifacts.
Keep them outside git. The CLI prints local audit details; a later public API
must use an appropriate restricted projection.

New tests cover separate-process approval/rejection and stable restart, duplicate
and conflicting decisions, stale revision, package mismatch, unlisted reviewer,
blank/oversize reason, invalid fields, blocked/incomplete output, independent
verification gate, interrupted-analysis hold, decision-before-checkpoint recovery,
concurrent case decisions, audit integrity and interrupt/package mismatch.
Full validation evidence is recorded in APPLY_M1_2.md.

Official interrupt reference checked 1 November 2026:
https://docs.langchain.com/oss/python/langgraph/interrupts

Next review: apply locally, run focused/full regression, demonstrate approval and
rejection. Commit on phase2/persistent-review-workflow only after those checks.
M1.2 remains In progress pending owner validation and hosted CI. Actual hours
must be supplied by the owner, never inferred from planned capacity.
