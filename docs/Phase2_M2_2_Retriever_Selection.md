# M2.2 retriever selection and frozen-note integration

6 November 2026. The comparison is based on the frozen M2.1 fixtures at
commit `6f826c9`. The [pilot instructions](Phase2_M2_1_Retrieval_Pilot.md)
reproduce the runs. [Per-question results](evaluation_results/m2_1) include
the policy-version manifests and timings.

| Method | Development Recall@3 | Development MRR@3 | Held-out Recall@3 | Held-out MRR@3 | Held-out median |
|---|---:|---:|---:|---:|---:|
| Stemmed TF-IDF | 92.9% | 0.857 | 100% | 0.893 | 1.17 ms |
| MiniLM | 100% | 0.917 | 100% | 0.845 | 9.07 ms |
| Hybrid RRF | 100% | 0.881 | 100% | 0.929 | 11.41 ms |

Each split has 14 answerable questions and two unsupported controls.
On the held-out set, hybrid's gain over TF-IDF came from moving Q29's
supporting passage from rank two to rank one. All three found all labeled
evidence within the top three. MiniLM and hybrid recovered the development
paraphrase miss Q04. All three returned candidates on unsupported controls.
The timing figures describe the recorded CPU run, including query encoding
and ranking, and exclude model loading. The Windows replay reproduced the
ranking metrics.

## Decision

Use stemmed TF-IDF for the frozen-note runtime. The held-out comparison
shows equal evidence coverage at the chosen cutoff. The observed hybrid
ranking gain is small, and the experiment does not show a downstream
publication benefit that justifies loading and operating an embedding
model for this corpus. Keep dense and hybrid available in the offline
comparison runner for future evaluation.

## Source binding and publication

The frozen-note tool indexes the v1.0 policy directory explicitly. It
validates the scan's policy hashes against captured memo bytes and uses
those same bytes for retrieval and the note-run provenance hashes. The
note report identifies the retrieval method and policy version. The
experiment's v1.1 corpus is evaluated separately. The tool response schema,
claim verification, deterministic composition and publication gate retain
their existing contracts.

The offline batch-note regression exercises a supported note, a memo edit
during a two-case run, and a submitted unsupported policy claim even though
the tool returned related passages. The latter must yield a blocked note
with no published answer. This is a fail-closed check with a scripted
provider response. Live model abstention rates remain unmeasured.

## Verify after applying the patch

From the repository root with the project environment active:

```powershell
python -m pytest -q tests/test_batch_notes_713.py
git diff --check
```

The M2.2 patch applies on top of the M2.1 checkpoint on
`phase2/persistent-review-workflow`. This check requires the project
development dependencies and runs without a model download or provider
credentials.
