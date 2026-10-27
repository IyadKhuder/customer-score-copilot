# Direct observed-recovery answers and readable monthly scores

Updated 2026-10-27. This follows the twelve-month descriptive-window patch.

For an observed-recovery question, a deterministic answer now appears directly
after the account heading and descriptive-window line. For showcase account
1000025 in December 1991:

> Yes, partially: the 12-month history shows recovery after a drop, but the latest score remains below its pre-drop level.

The supporting observations still report the 130-point drop, 89-point rebound
and 41-point shortfall. Formal reinstatement eligibility remains separate.

A definite yes/no is not forced where unsupported. Missing observations,
flagged data-quality issues and mixed patterns receive an explicit limitation.
Improvement without a preceding observed drop is described as improvement,
not automatically as recovery. The inverse showcase profile is distinguished
from a clear drop-and-recovery pattern. All answers are bounded to the stated
12-month window, with no claim about older history or operational clearance.

The recovery answer is computed from the validated snapshot and the existing
descriptive classifier's structured outcome. It is not extracted back out of
rendered prose and never depends on the provider's wording. It appears only
for recovery questions; other reviews retain the descriptive details.

Every account review now prints its reported observations one month per line:

```text
Reported scores (12-month descriptive window):
1991-01: 346
1991-02: 351
1991-03: 347
...
```

Policy definitions, windows, outcomes, provider controls and verification gates
are unchanged. No database rebuild, new dependency, Slack permission or
additional provider request is needed. Both scans-only and live-note modes
use this presentation; the existing message splitter still handles long replies.
