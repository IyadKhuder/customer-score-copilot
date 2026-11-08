# M3.1 — local API and Docker

Date: 7 November 2026. Base: `0831c2f`, branch `phase2/persistent-review-workflow`.

Status: completed at `e27561f`, based on owner-confirmed local Docker persistence/decision checks and all five GitHub Actions jobs passing on 7 November 2026. The preparation-stage verification notes below retain their original context.

## What this checkpoint teaches

An HTTP adapter translates network requests into application operations. It does not own policy rules or decide whether a package is approvable. The application saves inputs and schedules work; LangGraph manages durable execution and the human pause; CaseStore owns integrity checks and atomic decisions.

Submission performs bounded snapshot I/O, not the score analysis. After it commits the case and request key in one SQLite transaction, HTTP returns `202 Accepted` and a `Location` header. A single worker discovers committed cases from the database. An in-memory notification is not the queue: restarting the process rediscovers queued work.

The worker is a thread inside the one Uvicorn process. An OS file lease prevents a second process from using the same case store. This is a local single-worker design, not a distributed queue, multi-replica service, or exactly-once provider-call implementation.

## The integration boundary

| Existing component | Role in this API |
| --- | --- |
| `review_workflow.py` | Reuse case structure, snapshot/package hashes, state transitions, safe failure handling, revision comparisons and decision audit |
| `review_graph.py` | Reuse durable input checkpoint, analysis node, review interrupt, decision validation and recovery after case/checkpoint write gaps |
| `case_application.py` | Capture real account rows/policy bytes; adapt a frozen deterministic scan to the workflow; durable request identity; worker and safe projections |
| `api.py` | Authentication, bounded JSON validation, status codes and HTTP operations |
| `batch_scan.py` | Reuse `_snapshot`, `_base` and `_classify`; no changed policy semantics. These private helpers are an explicit coupling to consider when refactoring the domain layer later |
| `slack_review_service.py` | Existing synchronous Slack behavior remains separate; it is not treated as a durable job runner |
| `batch_notes.py` | Inspected, unchanged, not invoked by the API. Its captured-policy TF-IDF path and publication gate remain the authority for optional live notes |

The M1 CLI still creates its original fixed `workflow-demo-001` teaching case. API cases use a separate default store, real synthetic account rows and an injected deterministic analyzer. The worker only discovers cases registered in its API request table; it cannot pick up unrelated teaching cases.

`new_case` extracts the shared case constructor. An optional `held_projection` lets this deterministic adapter retain a safe incomplete scan. The default M1 behavior still discards unverified draft text. The API never supplies a model draft to this projection.

## Scope of analysis and verification

One account, market 1, first day of an assessment month, policy baseline v1.0. Histories are captured up to that month; at most 1,200 selected observations are accepted per case. Seven policy memo files are captured as exact UTF-8 bytes (hex in private storage), with SHA-256 hashes and a 128 KiB total size limit. Case detail returns their frozen text, source filenames and matching hashes for evidence review. The request, rows, account-existence/date diagnostics, policies and implementation hashes are bound into the immutable snapshot hash.

The worker uses the existing compiled deterministic policy evaluator. Captured policy text is provenance, not a dynamically executed policy language or an LLM retrieval result. Successful `attention_required` and `no_signal_in_scope` scans produce `verification_status=passed` and pause for analyst review. Here **passed means the deterministic package completed**, not that an LLM note was verified. `analysis.claims` is empty, `analysis.answer` is null, and `narrative.status` is `not_requested`.

Unsupported markets, missing accounts and insufficient observations produce a held package with an incomplete verification status. A missing assessment-month observation is held. A computed review indication can still carry coverage/data-quality limitations; inspect its scan outcome, reason codes and coverage rather than interpreting `passed` as evidence completeness. Invalid stored histories produce a safe failed case.

Live notes are disabled even if provider credentials happen to exist in the environment. The Slack allowance/pacing budget is process-local. Enabling durable live notes later requires persisted started attempts and handling of uncertain provider outcomes. No downstream live-model abstention measurement is added here.

Approval accepts the exact saved demonstration package. It does not register/clear warnings, reinstate an account, authorize lending or send a notification.

## HTTP contract

All case operations and `/openapi.json` require `Authorization: Bearer <token>`. Health endpoints are unauthenticated and reveal only readiness/mode information. Set `COPILOT_API_TOKEN` to a random ASCII token of at least 32 characters before startup; configuration fails closed otherwise.

This local demo maps its one token to `demo-analyst`. Identity is server-derived; a `reviewer` field in JSON is rejected. All token holders have the same analyst access. Per-user login, separate roles, TLS and hosted authentication belong to the hosting milestone. Keep this service bound to localhost.

| Method and route | Input / result |
| --- | --- |
| `POST /cases` | Required `Idempotency-Key` header; JSON below. Returns 202, saved case projection and Location. Replay returns the same case's current saved state, without recapturing sources |
| `GET /cases?limit=50&offset=0` | Bounded list, newest submission first. Maximum limit 100, offset 100000. No status filter yet |
| `GET /cases/{uuid}` | Saved status, revision, snapshot/package hashes, rows, deterministic analysis, source hashes, limitations and safe reason codes |
| `POST /cases/{uuid}/decisions` | Version/hash-bound approval or rejection; returns updated safe projection |
| `GET /cases/{uuid}/evidence` | Safe approved projection, still authenticated. Pending/rejected packages return 409. It does not publish externally |
| `GET /openapi.json` | Authenticated generated API schema; interactive Swagger UI is not included |
| `GET /health/live` | 200 while HTTP process is alive |
| `GET /health/ready` | 200 when worker is alive and case/source databases are readable; 503 on failure |

Submission body:

```json
{"account_id":1000003,"market":1,"as_of_period":"1991-12-01"}
```

Decision body (copy revision/hash from the case you actually reviewed):

```json
{"expected_revision":2,"package_hash":"<64 lowercase hex characters>","decision":"approved","reason":"Reviewed recorded evidence and limitations"}
```

`market` defaults to 1; account and period are required. Extra fields are rejected. IDs and revisions are strict integers (booleans do not count). The request body is limited to 8 KiB, including streamed bodies. Reasons must contain non-whitespace text and at most 1,000 characters. Idempotency keys accept 1–80 ASCII letters, digits, `_`, `.`, `:`, `-`; they are scoped to the server-derived analyst identity.

Missing/invalid token: 401. Unknown/noncanonical UUID: 404. Invalid fields/key: 422. Oversized body: 413. Reused key with different request, stale revision, wrong package, premature/duplicate/conflicting decision: 409. Source/storage unavailable: 503. Unexpected failure: safe 500. Errors do not echo submitted values, paths or exception text.

Response projections exclude private decision records, reviewer identity, reasons, event history and graph internals. Authenticated case detail includes the safe deterministic result before approval so an analyst can actually review it. The approved-evidence endpoint adds the approval gate for consumers who need accepted packages. It is not an unauthenticated public endpoint.

## Persistence and recovery

The case and its idempotency mapping commit atomically. A crash before graph initialization leaves a discoverable queued case. Graph initialization is repaired by the worker. Analysis is performed from captured rows; source edits or removal after submission cannot alter the saved inputs or package.

A queued case whose analysis implementation files changed is held. Existing packages remain readable, but a decision is refused after an implementation change; create a new case under the new implementation. Changes to the live source database/policy directory do not invalidate a correctly frozen historical package: its decision remains bound to the captured snapshot.

A saved `running` case without analysis is held as `INTERRUPTED_ANALYSIS_NO_RESULT`; it is not silently retried. If analysis committed before its graph checkpoint, recovery uses saved analysis without re-execution. If a decision committed before graph completion, the existing M1 graph recovery completes from the saved decision.

An unexpected worker exception stops it and fails readiness, preserving records. Inspect the saved state and diagnostics before restarting; do not edit hashes/revisions or automatically reset held work to queued. Repeated reads and restarts do not rerun completed analysis. No automatic delivery or external side effect exists here.

The API uses an additive `api_requests` table and existing `review_cases`/LangGraph tables. Use its own store (`var/api/cases.db` locally), separate from `data/scores.db` and previous teaching stores. The OS lease is local filesystem protection, not a safe lock across hosts or network filesystems.

## Docker approach

The Dockerfile starts from `python:3.11-slim`, installs the pinned API/workflow dependencies, verifies/builds the committed default synthetic database, then runs as UID/GID 10001. The source database and policy inputs are image data; the runtime writes only workflow state to `/app/var/api`.

Compose binds `127.0.0.1:8000`, injects the token at runtime and mounts the `case-state` named volume. An explicit build-context allowlist excludes `.env`, credentials, local SQLite files, git history and generated artifacts. No provider key is required or configured by Compose. The Python base image tag is not digest-pinned; this is reproducible application setup, not a byte-identical image guarantee.

One Uvicorn process owns one worker thread and one case-store lease. Do not increase workers, run multiple replicas, use reload for persistence demonstrations or mount the same state from another host.

The image health check calls readiness. Use `docker compose up --detach --force-recreate --wait` to replace the container while retaining the volume, then run the smoke test's resume phase. `docker compose down` preserves the volume; the destructive `-v` option is not part of this walkthrough.

Official references checked for this checkpoint:

- [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/) — own worker startup/shutdown through application lifespan.
- [FastAPI container deployment](https://fastapi.tiangolo.com/deployment/docker/) — explicit Uvicorn command and container lifecycle.
- [Docker volumes](https://docs.docker.com/engine/storage/volumes/) — state independent of container replacement.
- [Compose services](https://docs.docker.com/reference/compose-file/services/) — environment, ports and volume configuration.

## Verification and remaining gate

The delivery guide includes the exact environment results and PowerShell commands. The new API tests exercise real SQLite and LangGraph checkpoints, HTTP validation/authentication, frozen inputs, concurrent idempotency, held evidence, decision atomicity and checkpoint-gap recovery. A two-phase stdlib HTTP smoke script also runs across separate Uvicorn processes.

CI adds a Linux container job: build, submit/pause, replace container preserving volume, then verify saved package, idempotency, stale/duplicate rejection and approved evidence. The existing Windows/Linux Python 3.11/3.12 regression matrix also installs API dependencies and runs its tests.

Docker is unavailable in the implementation workspace. A passing Python/HTTP test is not a passing Docker run. M3.1 remains In progress until owner-side container execution succeeds; GitHub Actions results must be observed after the owner's push.
