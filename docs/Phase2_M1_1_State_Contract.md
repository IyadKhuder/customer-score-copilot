# Phase 2 M1.1, step 1: persistent state contract

Date: 31 October 2026. Base: a4b39ae6c0efbb993b46301e1c905684ff1a74ab.

## What this step teaches

A state is a serializable description of a case. A transition changes that
state only when the current status and revision permit it. Persistence writes
that state to disk so a new Python process can read it. A revision is an
optimistic concurrency check: save only if no other update has intervened.

This isolated example uses Python's built-in SQLite library. It neither imports
nor changes Slack, the account database, policy retrieval or provider adapters.
Its invented three-score case does not represent an existing customer.

## Contract

| Field | Meaning |
| --- | --- |
| schema_version | State schema, currently 1 |
| case_id / revision | Stable UUID / incrementing state revision |
| workflow_status | queued, running, awaiting_review, held, failed |
| verification_status | not_requested, passed, blocked, incomplete |
| analyst_decision | pending in this step; no decision API yet |
| evidence / provenance | Frozen invented input and demo policy/config/code identifiers |
| snapshot_hash | SHA-256 of canonical evidence plus provenance |
| analysis / package_hash | Saved structured claim and composed answer, bound to snapshot and verification |
| timestamps / events | UTC creation/update times and committed transition history |

| From | Operation | To | Condition |
| --- | --- | --- | --- |
| queued | resume | running | Matching revision |
| running | save analysis | awaiting_review | Analysis exists and verification passed |
| running | save analysis | held | Verification blocked or incomplete |
| running | restart with no saved output | held | Explicit inspection required; no automatic replay |
| running | analysis exception | failed | Persist failure status without raw exception text |
| awaiting_review / held / failed | show or resume | unchanged | Return saved state without analysis |

Other transitions raise TransitionError. Terminal recovery and decisions are
added separately in M1.2. Both approved and rejected packages will eventually
have workflow status completed, while preserving their distinct analyst decision.
Approval concerns the exact review package, never a credit or account action.
A failed verification cannot be overridden by approval.

Hashes detect accidental state/package mismatch; they are not signatures or
protection against a person who can rewrite both data and hashes. This is one
local worker, with no authentication, durable event deduplication, live-call
accounting, delivery or production recovery guarantee. Do not use the store for
sensitive data. Use a separate local SQLite path, outside the repository.

## Analysis boundary and restart behavior

The example analysis function returns one structured endpoint-difference claim,
checks it against frozen input, and composes deterministic text. This exercises
the workflow protocol; it does not invoke or replace the project's existing
policy/claim verifier. Existing verified analysis remains one future node.

A committed result is never recomputed on resume. If the process dies before
committing its result, the persisted running state moves to held on restart.
This conservative rule avoids teaching that checkpointing means exactly-once
execution. An analysis node and a later human-review interrupt must be separate.

## Framework compatibility investigation

Official docs consulted on 31 October 2026:
- https://docs.langchain.com/oss/python/langgraph/persistence
- https://docs.langchain.com/oss/python/langgraph/interrupts
- https://reference.langchain.com/python/langgraph.checkpoint.sqlite

LangGraph uses thread IDs and durable checkpointers to resume. Interrupt nodes
restart from their beginning. In-memory saving does not survive restart.
The SQLite reference recommends strict msgpack deserialization.

A Linux Python 3.12 pip dry-run against the unchanged requirements-dev.txt and
constraints.txt succeeded with langgraph 1.2.19 and checkpoint-sqlite 3.1.3.
That is dependency resolution evidence only, not installation/runtime testing,
Python 3.11 or Windows validation. No dependency files changed in this step.
Next: check the target Python 3.11 environment, pin the workflow extra and its
transitives, then add and test a durable LangGraph adapter using this contract.

## Verification and progress

Six focused unittest tests passed on Linux Python 3.12.14, including independent
CLI processes, no recomputation, stale/invalid transitions, blocked analysis,
interrupted running state, failure persistence and snapshot mismatch.
The user's pre-change Windows baseline is 1549 passed, 1 skipped, 169 subtests.
It has not been rerun here. Run the focused tests and full regression locally.

M1.1 remains In progress: this is its first plain-Python teaching step; the
LangGraph adapter and existing-analysis binding remain outstanding.
P0 is complete based on the reviewed blueprint and user-reported fresh baseline.
The 120-hour capacity uses five flexible workdays/week, including weekends.
Actual work hours must be supplied by the owner; today is not estimated.
