# M1.1 step 2: durable LangGraph adapter

Date: 1 November 2026. Starting checkpoint: 5e55b91.

## Concepts before code

The case record answers: what evidence, analysis and verification belong to this
review? The graph checkpoint answers: which step should execute next? Neither
is an analyst approval. Both live in separate tables of one local SQLite file.

| Concept | Meaning in this example |
| --- | --- |
| State | case_id, frozen snapshot hash and graph version; serializable references only |
| Node | A Python function performing a bounded step |
| Edge | The allowed next step; blocked analysis routes to END |
| Thread | Checkpoint history identified by this case's UUID |
| Checkpointer | SqliteSaver persists the graph's position across Python processes |
| Interrupt | await_review pauses and exposes package identity for later human input |
| Case revision | Existing optimistic check on case writes; not a graph checkpoint ID |

The topology is START -> analyze -> await_review -> END, with an alternate
analyze -> END edge for held/failed cases. Creation deliberately pauses before
analyze using a static breakpoint. The first resume continues with None input.
await_review then calls interrupt() dynamically. Further resume calls through
the CLI inspect the paused result; they do not submit a decision.

LangGraph normally restarts an interrupted node when given Command(resume=...).
Analysis is a separate node, before the review interrupt. M1.2 will introduce
version-bound decisions. This patch deliberately has no decision command.

## Design choices

The previously tested review_workflow.py remains unchanged. analyze wraps its
resume operation, treating claim generation, deterministic verification and
composition as one node. The operation is still the invented three-score
teaching fixture, not the main policy analysis pipeline. It makes no LLM calls.
We retain the claims-first structure without claiming that this simple numeric
check replaces the project's policy/claim verifier. Binding the production
analysis path remains separate work.

The graph stores a reference and snapshot hash, not another editable copy of
analysis. Every inspection validates case identity, graph version, snapshot
binding and the existing case/package integrity checks. A changed graph contract
must not silently reuse old checkpoints. No migration of old CLI-only cases is
provided: create a new graph demo case in a fresh local file.

A synchronous durability setting writes checkpoints at graph boundaries.
This is not an exactly-once guarantee. Case and checkpoint commits are separate:
- A case result may commit before the analyze checkpoint. Re-entering analyze
  then reuses the saved result without recomputation.
- A running case without a committed result becomes held, never auto-replayed.
- A blocked/incomplete package becomes held and does not enter human review.
- An analysis exception records failed and ends the graph.
- A crash between case creation and initial checkpoint may leave an orphan case;
  inspect it, then create a new demo case. Automatic repair is outside this step.

Only one local worker is supported. Revision checks do not make simultaneous
graph invocations safe. There is no authentication, live-call accounting,
notification, multi-worker lock, checkpoint retention or database backup system.
Approval/rejection and recovery controls come in M1.2. Use only invented data.
Hash integrity detects accidental mismatch, not an attacker who can rewrite the
file. SqliteSaver uses a strict serializer with an empty custom-module allowlist
and no pickle fallback. Context-managed SQLite connections close on exit.

## Dependencies and compatibility evidence

requirements-workflow.txt is an optional install for the graph. Runtime and
Slack requirements retain their existing package selections. requirements-dev.txt
includes workflow dependencies so the existing Windows/Linux x Python 3.11/3.12
CI matrix exercises the graph tests, rather than skipping them silently.
constraints.txt adds exact pins for resolved workflow dependencies, preserving
existing pins. This adds LangChain infrastructure transitively; no agent/model
API is required, and no LangSmith account or API key is needed.

Selected versions: langgraph 1.2.19, checkpoint-sqlite 3.1.3, checkpoint 4.3.1.
Resolver checks succeeded for a Python 3.11 target and Windows CPython 3.11
wheels. These were pip cross-target checks performed on Linux; they do not
execute Windows or Python 3.11 code. Local runtime tests use Linux Python 3.12.14.
The owner's Windows/Python 3.11.9 tests and hosted CI remain release gates.

Official references checked 1 November 2026:
- https://docs.langchain.com/oss/python/langgraph/checkpointers
- https://docs.langchain.com/oss/python/langgraph/interrupts
- https://reference.langchain.com/python/langgraph.checkpoint.sqlite

## Verification

14 focused tests passed (six existing workflow tests and eight graph tests).
They cover independent CLI processes, unchanged saved result/checkpoint, completed
case before graph checkpoint, uncertain running case, blocked/failure routing,
missing checkpoint and graph version/snapshot mismatch. pip check passed.
Full regression results are recorded in APPLY_M1_1_STEP2.md.

M1.1 remains pending local validation. No merge, commit, push, live provider call
or Slack modification was performed while preparing this package.
