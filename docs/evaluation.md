# Evaluation notes — Module 4.10 policy refinement

**Behavioral Score Trajectory Copilot**
**Experiment recorded:** 10 October 2026
**Document status:** Portfolio-facing review of an exploratory baseline, **not** a formal benchmark or operational policy validation.

## Objective

Investigate whether enriching an ambiguous policy knowledge base helps a tool-calling RAG agent analyze score trajectories more faithfully. The question was held constant:

> Why did account 103480 drop bins, and what should we do?

Each of two LLM provider paths (Gemini and Anthropic Claude) was tested with **policy v1.0** and **experimental v1.1**. The intended within-provider intervention was the policy corpus; the same agent, system prompt, score dataset, and numerical tool registry were used. Provider-generation variability was not controlled and there was only **one recorded run per condition**.

The numbers below are transcribed from `results/module_4_10/metrics_summary.csv` and cross-checked against `comparison_report.md`. The four raw JSON runs were **not independently re-executed** for this documentation update.

## Versioned policy intervention

| Aspect | Baseline v1.0 | Experimental v1.1 |
| --- | --- | --- |
| Documents | Seven original fictional policies | Seven fictional policies; POL-002, POL-003, POL-006 revised |
| Risk bands / numeric thresholds | Original | Preserved |
| Warning-record closure | No explicit clearance workflow | Manual analyst closure, adopted **for the synthetic project only** |
| Rolling event-window semantics | Ambiguous | Explicit *t−2, t−1, t* **working interpretation** |
| Decline streaks, volatility, escalation interpretation | Incomplete or ambiguous in relevant places | Clarified as **provisional working interpretations**, not operational approvals |

**Decision boundary:** The project approved only the manual analyst-clearance design decision. The rolling-event window, third-decline counting convention, six-score population standard deviation, distinct-criterion escalation interpretation, and Standard-band limit clarification remain experimental assumptions. None are represented as an actual lending institution's policy.

The score tools do **not** expose a warning register. Hence even under a rule requiring manual closure, a particular account's open/closed record status is **unknown without register evidence**. A normative rule is not evidence that an account has an open warning.

## Execution metrics

| Run | Completed | Tool rounds | Score/market calls | Retrieval calls | Total calls | Returned passages* | Distinct policy memos | Metadata-only passages |
| --- | :---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Gemini v1.0 | Yes | 2 | 3 | 2 | 5 | 6 | 4 | 0 |
| Gemini v1.1 | Yes | 4 | 3 | 4 | 7 | 12 | 5 | 3 |
| Claude v1.0 | Yes | 3 | 5 | 4 | 9 | 20 | 7 | 0 |
| Claude v1.1 | Yes | 2 | 4 | 6 | 10 | 30 | 7 | 2 |

*Passages include repetitions; distinct policy memos measure document coverage, not relevance or precision. All four runs returned a nonempty answer. Counts are internally consistent (`total calls = score/market calls + retrieval calls`). No comparable end-to-end latency, token cost, or stable exact model-revision data were captured.

### Retrieval findings

The comparison report records that all four responses retrieved **POL-002**. However:

- **Gemini v1.1** did not retrieve the dedicated v1.1 look-back, manual-closure, or Standard-band clarification passages. Three of its twelve returned passages were metadata-only; the model had incomplete evidence even though it made more retrieval calls.
- **Claude v1.1** retrieved those dedicated v1.1 passages. Two of its thirty returned passages were metadata-only.
- **POL-005** (data quality) was retrieved by both Claude runs but by neither Gemini run.

These observations show why counts of returned chunks are a weak proxy for the specific rules needed to answer a question.

## Ground-truth case notes: account 103480

As recorded in the source comparison report:

| Fact or interpretation | Reported reference |
| --- | --- |
| Observation range | 23 monthly periods, June 2023 through April 2025 |
| Starting state | Score 611, bin 7 (Prime) |
| Ending state | Score 574, bin 6 (Standard) |
| Net movement | −37 points |
| Recorded migrations | April 2024: 7→6; May 2024: 6→7; October 2024: 7→6 |
| Latest reporting month | April 2025; decline streak no longer ongoing |
| February 2025 | Historical third consecutive monthly decline occurred; qualifies inside Feb–Apr under the v1.1 **working** window |
| Warning-register status | Unknown / not verifiable from current score tools |

This case exposes three *different questions* that the LLM must not conflate:

1. **Historical event:** Did a qualifying decline event occur in the look-back?
2. **Ongoing condition:** Is a decline streak still occurring in the latest month?
3. **Administrative record:** Is a previously logged warning currently open or closed?

For this case under the experimental interpretation: **yes** to a qualifying February event inside the February–April window, **no** to an ongoing April decline, and **unknown** to the administrative status.

## Qualitative observations

| Capability | Gemini v1.0 | Gemini v1.1 | Claude v1.0 | Claude v1.1 |
| --- | --- | --- | --- | --- |
| Principal numerical trajectory | Correct | Correct | Correct | Correct |
| Retrieved POL-005 data-quality rule | No | No | Yes | Yes |
| Interpreted Feb event inside Apr look-back | v1.0 ambiguous | Missed | v1.0 ambiguous | Correct under v1.1 assumptions |
| Manual closure / policy gap | Identified v1.0 gap | Incomplete v1.1 evidence | Identified v1.0 gap | Correct separation of rule and unverifiable record status |
| Avoided unsupported Standard-band limit ban | Yes | Yes | No | Yes |

**Conclusion bounded to these four outputs:** Claude v1.1 was the most complete on the specific policy ambiguities targeted by v1.1, but not consistently superior on every dimension. Claude v1.0 included additional market comparison context that its v1.1 run omitted. The corpus intervention cannot be isolated from model/tool-selection randomness in a one-run-per-cell design.

### Additional weaknesses documented in the report

- Historical six-period volatility was not evaluated consistently. A last-window volatility value cannot establish that **no** earlier window exceeded a threshold.
- One Claude v1.0 answer imprecisely described elapsed months; one made a derived-volatility estimate without deterministic tool support.
- POL-007's site-level downward-migration criterion was **not assessed** by the current score tools, so it must not be labeled definitively false.
- The April 2024 reversal pattern was recognized by Claude but not Gemini in the recorded runs.
- Numeric evidence appearing somewhere in a tool result would not by itself validate the **meaning** of a claim or the appropriateness of an operational recommendation.

## Limitations / provenance

These four exploratory records do **not** establish a provider ranking or a statistically significant policy-improvement effect. The archive does not independently pin precise model revisions, fixed random seeds, a lockfile, timestamps, full latency/cost/token totals, or repeated trials. No re-running or independent raw-JSON verification was performed when preparing this document from the supplied report/summary/decision log.

The original policies and experimental v1.1 snapshots should remain versioned separately. Do not overwrite baseline reference results after tuning prompts, chunking, or tools: use new experiment identifiers.

## Reproducible next evaluation protocol

1. **Freeze** corpus versions, code revision, score data, test questions, provider model IDs, evaluation rubrics, and runtime settings; record timestamps and environment/dependency versions.
2. **Separate retrieval testing** from full agent testing: label must-retrieve passages for each question, then measure recall@k, relevance, and metadata/chunk pollution.
3. **Add deterministic temporal oracles** for event dates, look-back semantics, rolling six-period volatility, continuous decline streaks, and open/closed status when register data is absent.
4. **Score answers by claim type:** numeric correctness, policy application, unsupported recommendations, missing evidence disclosure, and artifact/data-quality handling.
5. **Repeat runs** per provider × corpus × case; report uncertainty, incomplete runs, tool errors, latency, usage, and cost. Compare interventions separately: corpus enrichment versus changed chunking versus embeddings or prompt changes.

### Suggested regression cases

- Qualifying February event in an April report with no ongoing April decline.
- Recovery without a warning-register closure record: do **not** claim the warning is closed.
- Standard-band account: do **not** invent a mandatory credit-limit freeze or guaranteed limit increase.
- Short-term reversal: retrieve and apply POL-005 before risk escalation.
- Historical six-period volatility: avoid using only the latest window to negate all historical triggers.
- Market-wide migration escalation: identify unavailable calculation instead of treating it as disproved.

## Source artifacts and publication plan

Local source package: `results/module_4_10/` (not yet committed to the public GitHub main branch during Phase 2). Relevant records include:

- `comparison_report.md` — qualitative case evidence and limitations
- `metrics_summary.csv` — per-run call and retrieval counts
- `decision_log.md` — approved project decision versus provisional assumptions
- `raw_runs/` — four archived JSON responses, intended for later curated release
- `policy_snapshots/` — separate baseline and experimental memo versions

A public publication should include a selected, sanitized, reproducible subset, rather than every debugging artifact. Any later performance figures must be traced to newly archived benchmark runs and a documented evaluation protocol.
