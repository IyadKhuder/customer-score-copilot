# M1.3: abstention reporting and role diagnostics

2 November 2026. This patch builds on c567194 plus the applied, uncommitted
M1_3_step2_bounded_live_pilot patch. It changes reporting and local diagnostics.
Prompts, fixtures, publication gates, model configuration and call limits remain
unchanged. It makes no provider calls during preparation or offline validation.

## Read numeric publication and abstention separately

The original reference_proposal_correct field actually measured whether a
proposal was a complete publishable numeric package. A justified abstention
therefore returned false. New reports retain this legacy boolean, add the clearer
reference_numeric_package_publishable boolean, and add reference_assessment:

| Assessment | Structured evidence criterion |
| --- | --- |
| publishable_numeric | Exactly the supported score and bin for the requested context, with no policy claims or limitations |
| justified_abstention | Requested observation absent, no score/policy claims, and at least one limitation |
| unjustified_abstention | The same empty-claim response with limitations, but the requested observation is available |
| incorrect_or_incomplete_numeric | Other structurally valid proposals that cannot form the required numeric package |
| invalid_or_unavailable | No structurally valid analysis proposal was obtained |

This assessment checks structured claims and evidence, not the semantic truth of
all draft prose or limitation wording. For example, a fabricated number hidden
in the unpublished answer string is not assessed by this metric. Public text is
still composed deterministically from verified claims. An empty claims list
without a limitation is incomplete rather than an explicit justified abstention.

justified_abstention and the corresponding summary count distinguish correct
insufficient-evidence behavior from incorrect numeric proposals. Numeric
publication still stays blocked on abstention. held_correct_proposals retains
its old meaning: holds on otherwise publishable numeric packages, including
reviewer failures. It is not a false-alarm count by itself.

Reports now carry plan.report_version = 2. Existing report files are never
rewritten or migrated by the live runner. Source hashes change because code has
changed; fixture and prompt hashes remain the same. This is a versioned change
to instrumentation, not a new observation of model quality.

## Specific failure diagnostics

The existing error_category remains available. New call records add failure_detail
(null on a valid response). Detail codes contain static labels only:

| Code | Failed check |
| --- | --- |
| response_shape_invalid | Missing/invalid choice or message shape |
| unexpected_tool_calls | Tool calls returned to this tool-free role |
| output_token_limit | Provider finish reason length |
| response_not_completed | Finish reason other than stop or length |
| response_text_invalid / response_text_empty | Nontext or empty content |
| response_size_limit | Text exceeds the existing 16 KiB role bound |
| json_invalid | Text cannot be parsed as JSON |
| json_duplicate_key / json_nonfinite_number | Strict JSON checks failed |
| analysis_contract_invalid | Analysis JSON violates the existing proposal contract |
| reviewer_contract_invalid | Reviewer JSON violates the existing findings contract |
| response_normalization_or_processing_failed | Response returned but another processing step failed |
| request_not_started / provider_request_failed | No request began, or the SDK attempt raised an exception |

Raw response checks now occur before adapter normalization so malformed tool
arguments or missing choices can be categorized instead of disappearing into a
normalization error. These checks do not add requests, retry, repair, or relax
output acceptance. A failed role still receives the existing hold behavior.

## Optional rejected text

The new --retain-rejected-output flag is OFF by default. When explicitly enabled
for a future live run, a rejected response with text may produce a local
rejected-NNN-role.json file in the new results bundle. Only its relative filename
is put in the report. The file has unverified=true and at most 4096 UTF-8 bytes
of retained_text, with truncated=true if shortened. JSON metadata/escaping may
make the physical file larger than 4096 bytes.

This text is untrusted model output. It is not sanitized for arbitrary private
content and is not a public answer. Keep it in ignored results and inspect it
locally. Do not commit it. The capture contains no request payload, environment,
SDK object or raw exception text. Provider exceptions alone create no text file.
A local capture-write failure is recorded separately and cannot make a failed
role valid. Existing rejection/hold rules still apply.

## Observed original pilot, 1 November 2026

These are owner-provided results from the ORIGINAL runner, not a rerun with the
new diagnostics. Smoke: completed, two requests. Full pilot: eleven requests,
completed_with_role_failures, exit code 2. Source unchanged was true. No top-level
execution errors or SDK request exceptions were recorded. Across smoke and pilot,
13 provider attempts were recorded in total. The remaining allowance was declared
by the owner and was not verified against account-wide provider usage.

| Evaluation | Observed result |
| --- | --- |
| Paired cases, both paths | Two publications from three cases; no incorrect publications or holds on complete correct numeric packages |
| LP-A2 missing month | Analysis supplied no claims and an explicit missing-month limitation; both paths held publication |
| LP-A2 reviewer | invalid_model_output; exact failed check cannot be recovered because the old runner did not retain it |
| Fixed challenges, both paths | Only the valid control published; all four flawed packages stayed blocked |
| LP-B2 wrong score | Reviewer matched unsupported_claim / score |
| LP-B3 missing bin | Reviewer matched missing_claim / bin |
| LP-B4 wrong month | Reviewer matched both context_mismatch findings |
| LP-B5 absent evidence | Reviewer requested inspection using unsupported_claim and missing_claim, rather than the rubric's evidence_gap codes |

LP-B5's exact-label mismatch is not a missed hold. unsupported_claim describes
unsupported values reasonably; missing_claim is questionable because claims were
present. The rubric remains unchanged so the original mismatch remains visible.
No semantic-equivalence scoring is added after seeing these results.

Ten of eleven full-pilot role outputs were valid: all three analysis outputs
and seven of eight reviewer outputs. The reviewer did not change publication
outcomes over the numeric deterministic baseline in this small pilot. This does
not establish that reviewers never help. Keep the simpler path selectable.

## Offline readback of LP-A2

scripts.inspect_live_proposal_phase2 reads a saved paired-case proposal and report,
checks the fixture hash plus both paths' evidence/proposal hashes, and prints the
current structured assessment. It does not write files, construct clients, make
provider requests or change the saved report. It accepts original report shapes.
It does not replay the old reviewer or recover rejected text. A mismatched or
incomplete bundle is refused rather than silently reinterpreted.

Follow APPLY_M1_3_DIAGNOSTICS.md for checks and readback. No live rerun is required
for this patch. M1.3 remains in progress pending final interpretation and the
combined Step 2 commit/CI checkpoint.

## Preparation checks

Linux/Python 3.12: 84 focused checks passed. Final full regression: 1,652 tests
and 169 subtests passed. The additional 23 checks cover abstention assessment,
specific role-failure codes, bounded opt-in capture, capture failures, and
hash-checked read-only inspection of an original-format saved report. The dry
smoke plan completed without provider calls. On the owner's Windows environment,
the existing symlink test may remain skipped as in prior runs.
