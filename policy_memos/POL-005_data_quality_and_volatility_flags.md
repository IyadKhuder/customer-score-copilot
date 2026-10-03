# POL-005 — Data Quality and Volatility Flags

Rapid, direction-reversing score changes (e.g., a drop followed immediately by a near-equal recovery within 1–2 periods) should be flagged as a potential data or scoring-model artifact rather than a genuine behavioral shift, and excluded from early-warning escalation pending manual confirmation. Any single-period change exceeding 150 points is treated as a probable data error by default and routed to data quality review before any risk action is taken.
