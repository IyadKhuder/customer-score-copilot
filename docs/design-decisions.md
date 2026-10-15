# Engineering design decisions

**Behavioral Score Trajectory Copilot** · Implementation and experiment snapshot: October 2026

This document records the main engineering choices behind the prototype: **the problem each choice addresses, the alternatives considered, the resulting trade-offs, and the evidence available so far**. It is written for technical reviewers rather than as a chronological development tutorial.

For component interfaces and runtime behavior, see [Architecture](architecture.md). For the exploratory four-run policy experiment and its limitations, see [Evaluation](evaluation.md).

> **Scope:** This is an AI-assisted credit-risk analysis demonstration using sample data and project-authored policies. It is not an automated lending system, a source of binding credit decisions, or an implementation of any lender's operational policies. “Approved” below refers only to decisions within the synthetic project.

## Decision summary

| ID | Decision | Status |
| --- | --- | --- |
| DD-01 | Separate numerical score facts from policy-language retrieval | Implemented |
| DD-02 | Expose a fixed registry of score tools instead of arbitrary SQL | Implemented |
| DD-03 | Start policy retrieval with TF-IDF and Porter stemming | Implemented baseline |
| DD-04 | Keep agent orchestration independent of model-provider SDKs | Implemented; exercised with two API styles |
| DD-05 | Treat policy clarification as a versioned, governed experiment | Experimental v1.1; original v1.0 preserved |
| DD-06 | Use synthetic scenarios to exercise analytically distinct cases | Implemented |
| DD-07 | Capture tool evidence now; add independent checks and systematic evaluation next | Evidence collection implemented; checks/evaluation planned |

## DD-01 — Hybrid evidence architecture

**Context.** A score explanation needs precise historical records and rules expressed in prose. A language model alone is not a reliable source of account-specific numbers; embedding score rows into a document index would also make precise calculations and filtering unnecessarily difficult.

**Decision.** Retrieve score histories and deterministic summary statistics via Python tools backed by SQLite. Retrieve credit-policy passages from a separate Markdown knowledge base. The LLM selects tools and synthesizes an explanation using both returned evidence types.

**Alternatives considered.** (a) Prompt the model with all records and memos; (b) use RAG for both numbers and text; (c) hard-code all explanations without an LLM.

**Trade-off.** This separation improves traceability and keeps each source suited to its data type, but introduces orchestration complexity. It does **not** guarantee that the model will use retrieved evidence correctly. The answer may still confuse a historical event with a current condition or infer an unsupported action.

**Evidence/status.** The current `src/agent.py` combines score-tool schemas with `retrieve_policy` and records tool outputs in `tool_evidence`.

## DD-02 — Constrained score-tool interface

**Context.** A free-form SQL-generation agent would have a larger error and security surface than needed for this proof of concept. The core analytical operations are well defined.

**Decision.** Expose six named functions in `src/score_tools.py`: `list_accounts`, `get_history`, `get_latest_score`, `get_migrations`, `compute_trend`, and `get_market_average`. Their SQL is predefined and parameterized; the orchestrator dispatches only registered tool names.

**Alternative considered.** Let the LLM generate and execute arbitrary SQL.

**Trade-off.** A smaller tool surface is easier to inspect and test, but is less flexible. For example, `compute_trend()` returns **full-history** score standard deviation. It does not by itself supply the last-six-period or every historical rolling-six-period volatility statistic needed for all policy checks. We should add explicit deterministic analytical tools rather than ask the model to infer unsupported historical calculations.

**Evidence/status.** The six functions and dispatch registry are implemented. Deterministic rolling-window policy checks remain future work.

## DD-03 — TF-IDF as a measurable retrieval baseline

**Context.** The initial policy corpus is small: seven authored Markdown memos. We wanted an inspectable, low-dependency retrieval baseline before introducing embeddings or a vector database.

**Decision.** Chunk Markdown with heading context, index passages with TF-IDF, apply Porter stemming, and rank results by cosine similarity. Return source filename, heading, passage text, and a relevance score.

**Alternatives considered.** Semantic embedding retrieval from the outset, or placing all policy text directly in the prompt.

**Trade-off.** TF-IDF is inexpensive, deterministic for a fixed corpus, and simple to debug, but relies heavily on lexical overlap. Porter stemming does not reconcile all semantically related forms—for example, *recovering* and *recovery* can receive different stems. Short version-metadata passages may also displace decision-relevant rules from the top retrieval results.

**Evidence/status.** The baseline retriever is implemented. In the Module 4.10 exploratory runs, Gemini with experimental policy v1.1 failed to retrieve the decisive manual-closure and event-look-back passages. Future embedding or chunking changes should be evaluated against a frozen set of retrieval questions, not assumed to improve results.

## DD-04 — Provider-neutral orchestration

**Context.** Provider SDKs differ in their message representation, tool-call format, and handling of tool results. Tying the agent loop to one vendor would make provider comparisons and later migrations harder.

**Decision.** Define project-owned `ToolCall` and `LLMResponse` objects and a common client interface (`chat` and `continue_with_tool_results`). Keep provider-specific transformations inside `src/llm_client.py`, selected via `src/config.py`.

The first live route uses **Gemini through an OpenAI-compatible endpoint**; the second uses **Anthropic Claude's native Messages API**. The choice balanced low-cost experimentation, widely reusable API compatibility, and testing against a genuinely different tool-use protocol. API credentials come from environment variables rather than committed source code.

**Alternatives considered.** Implement the orchestration directly against one vendor SDK; or build a much larger multi-provider framework before running the first complete agent.

**Trade-off.** An adapter adds code to maintain, and “provider-neutral” does not mean all models behave identically. Tool selection, answer quality, token accounting, limits, and errors can differ. Provider configuration remains explicit, not automatic model switching.

**Evidence/status.** Both API styles executed complete agent runs. That validates the current adapter boundary for these integrations, not universal compatibility with every provider.

## DD-05 — Versioned policy refinement with explicit governance

**Context.** RAG cannot reliably retrieve a rule that is absent from the corpus, and correct retrieval cannot resolve genuinely ambiguous policy wording. The original **v1.0** memos left important questions open, including what clears a previously logged early warning and how to distinguish a qualifying event in a look-back period from a decline still ongoing today.

**Decision.** Preserve the original corpus and test a separate **experimental v1.1** knowledge base. Keep policy changes auditable rather than silently editing v1.0. Within this synthetic project, **manual analyst clearance** of logged early-warning records was explicitly adopted: recovery or the passage of time does not automatically prove that a recorded warning has been closed.

Other v1.1 details remain **working interpretations**, not approved operational policy: the reporting-month look-back convention (`t−2`, `t−1`, `t`), counting consecutive-decline events, population standard deviation over six scores, and distinctions between analyst and committee escalation criteria. The tools have no warning register, so they cannot establish whether a particular warning entry is administratively open or closed.

**Alternatives considered.** Amend the original memos in place; bake clarified thresholds into the agent system prompt; or leave ambiguity implicit in model outputs.

**Trade-off.** Versioning adds review and maintenance overhead but protects experiment provenance and distinguishes business-policy decisions from model reasoning. Knowledge-base refinement must not be presented as validation of an institution's actual rules.

**Evidence/status.** The local Module 4.10 experiment compared both providers on v1.0 and v1.1. The experimental corpus and archived results were not part of the initial core-source GitHub commit; publication should preserve their labels and provenance.

## DD-06 — Synthetic scenarios rather than generic random examples

**Context.** A portfolio demonstration needs repeatable and analytically meaningful cases without depending on confidential customer data. Purely random trajectories may not exercise the warning, recovery, and data-quality rules.

**Decision.** Use the approved sample score history together with two reproducibly generated synthetic markets. Plant distinct events such as sustained multi-bin deterioration, repeated declines, a temporary spike, and decline followed by recovery. Store the combined sample as `data/scores_extended.csv`.

**Alternative considered.** Test only arbitrary accounts or purely random market simulations without known events.

**Trade-off.** Planted cases provide clear regression targets and broader rule coverage, but they are not representative evidence about real borrowers or model performance in production. The synthetic generation logic and scenario expectations should be documented when results are published.

**Evidence/status.** The current sample comprises **634 records, 33 accounts, and three markets**. Synthetic market generation uses a fixed random seed. The account-level case studied in Module 4.10 remains an exploratory test, not a statistical validation dataset.

## DD-07 — Evidence capture first; verification and benchmark discipline next

**Context.** An LLM can call the right tools and still make unsupported claims. Counting returned policy passages or checking whether some number appeared anywhere in a tool result is insufficient to validate temporal interpretation or operational recommendations.

**Decision.** In the implemented agent, collect each tool's name, arguments, result, and call ID; return `answer`, `completed`, `tool_evidence`, and `tool_rounds`; and bound the tool loop with a configurable maximum. Keep **post-generation numeric, policy, and temporal checks** as an explicitly planned reliability layer rather than claiming they already run.

The evaluation roadmap calls for frozen questions and expected outcomes, tests of retrieval recall and policy grounding, checks for historical-versus-current warning logic, repeated model runs, and consistent capture of latency, token usage, errors, and estimated cost.

**Alternatives considered.** Rely only on prompt instructions; treat completion or retrieval-call count as a quality score; or present a single successful demonstration as a benchmark.

**Trade-off.** Evidence logging makes failures diagnosable but not automatically correct. A proper evaluation harness requires additional test fixtures, scoring logic, and repeated trials.

**Evidence/status.** The Module 4.10 exploration has **one run per provider × policy-version condition**: Gemini v1.0 (5 tool calls), Gemini v1.1 (7), Claude v1.0 (9), and Claude v1.1 (10). All four completed. Claude v1.1 handled several policy clarifications better in this particular case, while Gemini v1.1 missed key passages. These observations **do not establish general provider superiority or a statistically repeatable policy improvement**; latency and cost were not consistently measured. See [evaluation.md](evaluation.md).

## What would cause us to revisit these decisions?

- **Retrieval:** measured semantic-retrieval or chunking improvements on the same frozen query set, including known failure cases.
- **Policy logic:** clarified, reviewed definitions of rolling windows, escalation, and warning-record closure, supported by deterministic checks.
- **Provider layer:** new API protocols or comparison requirements that cannot be implemented cleanly behind the existing client interface.
- **Quality controls:** verified failure modes that require claim-level provenance checks, warnings, or refusal when evidence is insufficient.
- **Operationalization:** only if a separately governed real-world use case provides authorized data, actual policy definitions, monitoring requirements, and human oversight.

The governing principle is **measure first, preserve provenance, and distinguish working prototypes from validated decision systems**.
