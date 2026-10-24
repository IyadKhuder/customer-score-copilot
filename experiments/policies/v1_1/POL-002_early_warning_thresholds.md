# POL-002 — Early-Warning Thresholds

**Version:** v1.1 experiment (synthetic project), 2026-10-10. **Not approved for production credit decisions.** Existing thresholds are unchanged. The manual-clearance governance rule was approved by the project owner on 2026-10-10; other interpretive/implementation conventions below remain working assumptions for controlled evaluation.

## Early-warning criteria (unchanged)

An account is flagged for early-warning review if any of the following occur within a rolling 3-month window: (a) a score drop of 40+ points in a single period, (b) a downward bin migration of 2+ bins, (c) three consecutive periods of score decline regardless of magnitude, or (d) volatility (standard deviation of last 6 periods) exceeding 35 points. Accounts meeting any single criterion are logged; accounts meeting two or more are escalated to a risk analyst within 5 business days.

## Rolling-window evaluation (working interpretation for the experiment)

For a report dated month *t*, the rolling 3-month **event look-back** includes reporting months *t−2*, *t−1*, and *t*, inclusive. Evaluate each criterion on the date it is met; a qualifying event stays visible in the event look-back even if the triggering pattern stops afterward. Report separately: (1) criterion met in the latest month, (2) criterion event occurring within the event look-back, and (3) historical events outside that look-back. Do not equate 'not met in the latest month' with 'no event in the rolling window'.

For criterion (c), a qualifying event is recorded on the third consecutive monthly score decrease. It requires four successive score observations to verify three month-over-month decreases. If decreases continue, criterion (c) remains ongoing in each later declining month; it counts as **one criterion type**, not multiple different criteria, for escalation-count purposes. A subsequent flat or rising month ends the ongoing streak but does not erase prior qualifying events. When monthly observations are missing, do not assume consecutive calendar months without checking continuity.

For criterion (d), use the six most recent monthly scores ending in the reporting month; if fewer than six observations exist, mark this calculation undetermined instead of assuming it does not trigger. **Provisional implementation choice:** use population standard deviation for this six-score measure, consistent with the current `compute_trend` tool. This calculation convention still needs explicit policy approval before production use. Site-specific threshold exceptions remain subject to POL-004, and potential data artifacts to POL-005.

## Logged-warning status and closure (NEW GOVERNANCE RULE — approved for this synthetic project)

Distinguish: **trigger event** (occurred on a date), **ongoing condition** (still occurring in the latest reporting period), and **warning record status** (open or formally closed). A reversal of a decline streak does **not** automatically clear a logged warning.

**Manual-closure rule:** once logged, a warning remains open until an authorized risk analyst reviews the available evidence and formally documents closure. The analyst must record the closure decision and its date. Ending the triggering condition, a score recovery, or the three-month look-back period expiring does **not** automatically close the record.

**Evidence limitation:** if the copilot's available tools do not provide the warning register and closure history, it cannot verify whether a historical warning is still open or has been formally closed. It must report the **warning record status as unknown/not verifiable**, while separately reporting any qualifying trigger events and whether the triggering behavior is ongoing. Do not infer a specific account's warning record status from score history alone.

This manual-closure rule was newly approved **for the synthetic project scenario** on 2026-10-10; it was absent from the original v1.0 memo. It is not an assertion about any real institution's policy.

## Escalation and related policy (working interpretation for the experiment)

Apply the unchanged POL-002 risk-analyst escalation when two or more **distinct criteria (a–d)** are met in the rolling event look-back; do not count repeated months of criterion (c) as multiple criteria. Separately, POL-007 calls for committee review when two or more early-warning criteria are met **in the same reporting period**, or under its other triggers. This rule concerns trigger occurrence; previously logged but unclosed warnings alone are not new same-period triggers. Apply POL-004 site exceptions and POL-005 data-quality review before operational action where relevant.
