# A shared twelve-month description and chart

Updated 2026-10-26. Implementation base: 279dea2.

All Slack account-review descriptions now use the same **12 calendar months
ending at the requested assessment month** as their charts. December 1991 means
January–December 1991; September 1991 means October 1990–September 1991. This
applies to canonical commands and supported natural-language questions, with
and without live notes, including text-only mode.

Each description states the exact start and end month, observed-month count
and that earlier events are outside this trajectory description. Later records
are excluded by the assessment cutoff. Net change refers to the first and last
available observations inside the displayed window, not to lifetime change or
the size of a rebound from a trough.

A complete descriptive window requires all 12 month starts. Missing months are
listed and remain chart gaps. No interpolation is performed, and a complete
12-month trend is not inferred across missing observations. The separate
"required policy review period" coverage line refers to the existing policy
baseline, which can be complete even when the longer display window has gaps.

## Clearer observed trajectories

A small deterministic descriptive heuristic recognizes a substantial drop
followed by smaller recovery moves, and its inverse. It does not consult account
IDs, scenario labels or the provider. Its conservative display criteria are:

- An observed one-month step of at least 40 points in either direction.
- An ensuing trough/peak and at least two movements back in the other direction,
  outnumbering setbacks and giving a net rebound/decline from the turning point.
- All movements after the turning point at most half the original step size,
  so equal-size oscillations are not presented as gradual recovery.

These are display criteria, not additional policy triggers or a formal recovery
eligibility test. Where both directions qualify, the larger initial step is used.
For broader improving/declining trends, smaller setbacks need not force "mixed":
net movement must be at least 40 points, favorable movements must outnumber
setbacks, and each setback must be at most one quarter of that net movement.
Ambiguous patterns retain a mixed description. Current data-quality flags take
precedence over a confident descriptive label.

For showcase account 1000025 as of December 1991, the output now identifies:

- A drop from 350 in April to 220 in May: -130 points.
- A subsequent recovery of 89 points to 309 in December.
- A final score still 41 points below April: **partial observed recovery**.
- Net change over January–December: -37 points (January started at 346).

Account 1000026 receives the opposite description: 222 in April to 352 in May,
then a decline to 263 in December. It remains 41 points above its April level.
The separate natural-language recovery qualification is placed after the review
so the observed result is presented before the formal-eligibility limitation.

## Policy and evidence boundaries

Policy evaluation windows, thresholds, warning/escalation decisions, coverage
checks, provider controls and claim-first publication gates are unchanged.
This change does not implement POL-006 reinstatement decisions. Policy signals
and LLM-note scope are separate from the descriptive 12-month trajectory.
A note may cite a shorter named policy window without contradicting the chart.

Both outputs use the same validated scan snapshot and a shared calendar helper.
There is no extra database scan, provider request, new permission, dependency or
data migration. Charts retain their existing 0–449 scale and missing-month gaps.
Your selected showcase database can remain in place.

Tests compare every one of the 26 market-1 showcase accounts' textual values
with its chart, exercise rolling cutoffs, missing months, partial and full
observed recovery, inverse patterns, small setbacks and oscillations, exclude
older/future observations, and verify unchanged policy outcomes. Source
snapshots are not mutated by description generation.
