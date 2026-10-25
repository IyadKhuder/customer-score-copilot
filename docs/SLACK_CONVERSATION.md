# Natural-language reviews and policy questions

Added 2026-10-25, based on implementation checkpoint d0d210c.

The Slack interface now recognizes common English account-review paraphrases
and answers questions about the demo's selected policy corpus. Unrelated,
ambiguous or unsupported requests receive an explanation and useful next step.
The existing exact command remains supported.

## Try these in the allowed channel

Start the listener with the same scans-only or explicit live-note launch command
as before. Mention the installed bot at the start of each message:

```text
@Score Copilot How did account 1000003 change as of December 1991?
@Score Copilot Could you review customer #1000003 in Dec. 1991?
@Score Copilot Why did account 1000026 trigger a warning as of 1991-12?
@Score Copilot Has account 1000025 recovered as of December 1991?
@Score Copilot What does risk bin 6 mean?
@Score Copilot Explain the warning thresholds.
@Score Copilot What is the recovery policy?
@Score Copilot Explain POL-007.
```

The two showcase IDs require the showcase database, as documented in
[SHOWCASE_TRAJECTORIES.md](SHOWCASE_TRAJECTORIES.md).

Account questions are normalized to the same validated one-account review.
The assessment month can be YYYY-MM or an English month name plus year. ISO
full dates, arbitrary date ranges, comparisons and missing/ambiguous cutoffs
are not silently inferred. Raw natural-language text is not forwarded to the
provider: only the validated canonical command reaches the review service.

A question about observed recovery receives the score review and a reminder
that observed improvement does not establish reinstatement eligibility.
"Why" questions explain selected rule indications; they do not identify the
underlying cause of score changes. Account-specific reinstatement, operational
clearance and future-score predictions remain unsupported.

## What happens in each mode

| Request | Scans-only | Live notes enabled |
| --- | --- | --- |
| Exact command or supported natural account question | Existing deterministic review and chart | Same review; eligible cases can add a verified LLM-assisted note |
| General demo-policy question | Attributed local policy excerpts | Same attributed excerpts; no provider request |
| Unrelated, incomplete or unsupported question | Friendly scope explanation or clarification | Same response; no provider request |

This first conversational increment uses a **bounded deterministic interpreter**
for common English wording. It does not add an LLM intent classifier or a
free-form model-written policy explanation. This keeps interpretation cost at
zero provider requests while allowing less rigid input. Unrecognized wording
may need rephrasing; unlimited natural-language understanding is not claimed.

Policy questions select one supported memo topic and use the existing TF-IDF
retrieval functions within that memo. The answer attributes the memo filename,
heading and selected version, and displays exact source excerpts. Retrieved
passages must match a chunk from the selected source snapshot before display.
The answer is labeled a demo policy reference, not a "Verified note" or an
account-specific decision. No account history is queried on this path.

Baseline v1.0 and experimental v1.1 remain separate. Select POLICY_VERSION
before launching and restart to change versions. Missing/unavailable policy
sources produce a friendly failure; the listener does not substitute a model's
memory or another version.

## Friendly boundaries

- A weather or general-knowledge question explains that the bot handles demo
  account reviews and policy lookups, then shows usage.
- A missing account or month gets a specific clarification.
- Multiple accounts, date ranges, extra ambiguous numbers and unsupported
  markets are rejected before the review service runs.
- "Did it recover?" asks for the account and month: **each message remains
  independent**. Thread memory and conversational follow-ups are deferred.
- Explicit help lists both account and policy examples.

The original channel restriction, event deduplication, greeting sanitization,
thread routing, analysis lock, chart failure behavior, account tools, provider
budgets and publication gates remain in effect. Valid account reviews still
use claim-first verification and deterministic composition for live notes.
Neither this interpreter nor a provider can override those gates.

## Verification

The tests exercise natural phrasing, named months, invalid dates and IDs,
unsupported requests, missing parameters, multiple accounts and markets,
exact attributed excerpts from each policy version, missing sources and
fabricated retrieval output. Fake Slack/provider tests check no calls for
rejected messages and policy questions, existing live publication/block/failure
behavior, shared allowance accounting, same-thread replies and deduplication.
No live Slack or provider requests are needed for these tests.
