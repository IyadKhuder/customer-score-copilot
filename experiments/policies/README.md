# Experimental policy corpus

The baseline corpus is `<project>/policy_memos/` at the project root, selected
by `POLICY_VERSION=v1.0`.

The tracked experimental corpus is `<project>/experiments/policies/v1_1/`,
selected by `POLICY_VERSION=v1.1`. Selection happens at process startup;
restart Python after changing it. Baseline CI and ordinary validation use v1.0.

The seven experimental memo files retain the reviewed snapshot bytes. Only
POL-002/003/006 differ from baseline. Manual analyst closure is a synthetic
governance addition approved by the project owner. Rolling-window, volatility
and eligibility clarifications remain marked working interpretations; do not
promote them to approved real-world rules.

No warning register or closure records are present in the score database.
An administrative warning state cannot be inferred from score movements.
Old worked examples and historical comparison outputs are excluded. This
folder preserves the distinct policy experiment, not old evaluation evidence.
