# Understanding the Slack response

2026-10-24

Replies lead with an observed score pattern, followed by the recorded scores,
review outcome and next step. No policy-memo knowledge is required to read them.

The pattern describes the account history, not the customer as a person or a
formal risk band. Labels are calculated without an LLM, from the scan's validated
snapshot. The bot never reads synthetic scenario labels to decide what to say.

## Display rules

- Use only the six calendar months ending at the requested month. Never display
  future scores or bridge missing months to assert a complete trend.
- Put active data-quality concerns first. Large swings may also be mentioned;
  the result is not a confirmed risk conclusion while quality checks are pending.
- With six complete months, describe severe/sudden drops only when the existing
  policy engine supplies an eligible event. The severity rules are unchanged.
- Otherwise describe non-increasing scores as declining, non-decreasing scores
  as improving, or constant scores as unchanged. At least one actual change is
  required for declining/improving.
- Broadly stable means mixed small movements within one score band and no warning
  from the existing rules. It is a descriptive display category, not a new policy
  threshold. Other mixed histories are described as moving both up and down.
- Incomplete six-month histories do not receive an overall stable/improving/declining
  label. Available observations and missing review months can still be shown.
- Improvement does not establish recovery or reinstatement eligibility. The bounded
  Slack scan still excludes recovery rules and causal explanations.

The six-month display window does not replace policy lookback windows or the
required baseline. Quality flags and rule outcomes use the existing engine's
windows, so they can refer to evidence beyond the six displayed observations.
Net change is descriptive and does not cancel a warning or quality flag.

## Try these synthetic accounts

Use `@Score Copilot review account ID as of 1991-12` with an actual bot mention.

| ID | Expected description |
| --- | --- |
| 1000001 | Broadly stable |
| 1000002 | Improving |
| 1000003 | Declining |
| 1000004 | Sudden score drop |
| 1000005 | Improving, without recovery/reinstatement approval |
| 1000006 | Possible data-quality issue and large swings |
| 1000007 | Possible data-quality issue |
| 1000008 | Severe score drop and committee-review next step |
| 1000009 | Incomplete monthly data |
| 1000010 | Requested month's observation is missing |
| 1000011 | Insufficient monthly history |
| 1208 | Account ID not found |

## AI notes and technical results

The score summary requires no LLM call and appears in both launch modes. Optional
AI notes still require the existing publication gate. Unpublished drafts, raw
exceptions and private audits are never rendered. Technical reason codes remain
in the service result for programmatic use; Slack shows readable explanations.
An unknown future reason uses a general manual-review fallback, not raw code text.

These wording changes do not change scanner decisions, policies, request limits,
provider settings or supported markets. The unknown-account improvement is retained.
Old screenshots document the previous interface; make a fresh screenshot/video
before illustrating the new wording. Do not relabel an old live-provider result.
