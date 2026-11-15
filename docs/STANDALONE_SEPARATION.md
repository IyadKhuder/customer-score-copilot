# Slack standalone separation

14 November 2026. Prepared from main at 3bb6bef1196770ba5979a4f8aada0595786d80ba.

## Repository boundary

Customer Score Copilot remains the bounded Slack/RAG application. Score Evidence
Review is separately published at https://github.com/IyadKhuder/score-evidence-review,
initial owner-confirmed commit 089a4ca07b3de9e212ee4420c4b58f6a7297f065, pushed with
successful independent CI. Its structured deterministic review runtime is distinct
from the Slack tool-calling/RAG path and from earlier live-role/retrieval experiments.

The branch m5/slack-standalone starts from existing main, not from the combined
Phase 2 branch. The original repository, tags and phase2/persistent-review-workflow
history remain preserved. No force-push, reset or history filtering is performed.
The original Phase 2 source remains pinned at
6f8fbf54be7bc349f44ad3810fbc9409e260a026. All 13 original checkpoints are owner-confirmed
complete. This document is a new separation record, not a rewrite of old evidence.

## Changes beyond main

The two files src/batch_notes.py and tests/test_batch_notes_713.py are carried
forward byte for byte from 6f8fbf5. Policy bytes are captured once, validated and
reused for retrieval and provenance. Two regression tests cover memo mutation
within a run and rejection of an unsupported related-policy claim. This prevents
losing a shared Slack fix by reverting to main alone.

README now presents the two independent applications and removes the obsolete
Phase 2 roadmap and technology claims from this project's stack. The original
Slack screenshots, video, setup, provider adapters, policy experiments, data,
MIT license and migration provenance remain intact. Both projects own their shared
synthetic foundations independently, with no submodule or runtime dependency.

The existing four-job Windows/Linux Python 3.11/3.12 CI matrix retains SDK imports,
verified synthetic setup, offline regression, scripted benchmark and separate-process
experimental corpus check. No FastAPI, LangGraph, frontend or case-container CI is
added here. Requirements remain main's Slack requirements unchanged. The workflow
name is clarified; historical workflow runs remain in GitHub.

The five owner-local docs/Azure_* diagnostic files are ignored and never staged.
Ignored .env, databases and private var/ receipts remain local. Apply through a
separate clean worktree, leaving the original Phase 2 checkout and its state in place.
The old Azure toolkit remains accessible in the preserved branch; running commands
against it requires that original checkout, not this Slack worktree.

## Verification scope

Assistant standalone results are supplied in the delivery package. Owner Windows,
live Slack smoke observations and this branch's new GitHub CI are pending until
reported. Existing Slack captures and earlier CI are historical evidence only.
No live model or Slack calls are authorized by this preparation package. Offline
checks use fake provider/Slack clients. New Slack smoke verification may run the
listener in local-scan mode without provider calls, after explicitly selecting a
verified synthetic database.

Score Evidence Review Operations and disconnect/reconnect observations remain
unconfirmed. The earlier owner-approved package/recreation/held-case demonstration
and successful CI do not imply those missing checks passed. No tracker hours,
Azure session or deployment is part of this separation.

## Assistant verification — 14 November 2026

Fresh standalone Linux/Python 3.12.14 virtual environment using this branch's
unchanged requirements-dev.txt: pip check passed; verified synthetic setup produced
72 accounts and 1,380 rows; 1,552 tests and 169 subtests passed. All six scripted
benchmark outcomes passed, including the correct insufficient-history block.
The v1.1 experimental corpus loaded in a separate process. OpenAI, Anthropic and
Slack SDK imports passed; FastAPI and LangGraph are absent from this environment.
No live Slack or provider calls, Windows execution, PowerShell execution, new
GitHub CI or Azure operation was performed by the assistant. Exact patch hashes
are supplied in the delivery inventory; these are uncommitted preparation results.

## Closure update — 15 November 2026

M5.1 functional verification is complete. Owner Windows checks, both independent
CI results, fresh Slack smoke and case-review Operations/disconnect/reconnect
observations are reconciled in [the closure record](M5_1_Closure_2026-11-15.md).
Earlier pending statements above describe preparation-time status and are
superseded by that dated record. Original extraction hashes remain unchanged.
