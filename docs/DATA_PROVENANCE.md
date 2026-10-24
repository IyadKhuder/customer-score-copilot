# Demonstration data provenance

Date: 2026-10-23

The dataset is independently invented for education and portfolio use. The
generator accepts configuration and fixed scenario definitions only. It does
not read old score files, fit parameters to them, shift their dates, perturb
their scores or preserve their trajectories. The fictional calendar is
constructed directly; its historical age is a presentation choice, not the
reason the data is independent.

The owner confirmed that all seven policy memos and their thresholds were also
independently invented for this project. They are demonstration policies, not
an institution's operational rules. Baseline v1.0 and experimental v1.1 remain
separate. The numeric score mapping is an additional demonstration convention.

| Bin | Inclusive scores | Band |
| --- | --- | --- |
| 1 | 0–49 | Sub-standard/Watch |
| 2 | 50–99 | Sub-standard/Watch |
| 3 | 100–149 | Sub-standard/Watch |
| 4 | 150–199 | Standard |
| 5 | 200–249 | Standard |
| 6 | 250–299 | Standard |
| 7 | 300–349 | Prime |
| 8 | 350–399 | Prime |
| 9 | 400–449 | Prime |

Every generated bin derives from its final score. Scores are fictional units,
not calibrated probabilities. Equal 50-point bins allow a severe three-bin
migration without necessarily exceeding the data-quality change threshold.

The 72-account default contains 11 fixed anchors in each market plus seeded
background scenarios. Families cover stability, improvement, decline, sudden
drop, recovery, volatility, data-quality candidates and severe drop. Three
coverage cases deliberately omit an interior month, omit the latest month or
retain only two observations. No missing months are imputed during loading.

| Profile | Accounts | Scheduled rows | Omissions | Actual rows |
| --- | ---: | ---: | ---: | ---: |
| Small | 72 | 1,440 | 60 | 1,380 |
| Large | 3,000 | 132,000 | 132 | 131,868 |

The default seed is 1729. Per-account SHA-256 counter streams make bounded
background variation independent of generation order and account-count
extension. This is stylized fixture construction, not a statistical claim
about a real population. Exact anchors do not depend on the seed.

`data/manifest.json` records generator/configuration/mapping identities,
source hashes, file hashes, market metadata and counts. `data/scenarios.json`
records construction membership and omissions. Sidecar labels never become
privileged evidence for the agent. A family can produce overlapping actual
policy signals; construction labels are not verified risk decisions.

The project preserves file bytes across Git checkouts with `.gitattributes`.
Automatic newline conversion would otherwise change source/policy/data hashes
on some platforms. Intentional source edits or line-ending edits require
regenerating the input identity; do not bypass a mismatched manifest.

The v2 benchmark uses six additional isolated histories in its own identifier
namespace, not records from the default CSV. Expected counts, statuses and
gate outcomes are fixed assertions checked against real policy/verification
code. New fingerprints prevent scoring old reports as replacement-data runs.

Low-level component tests also contain independently authored artificial
threshold/control inputs. Some intentionally use broader values, independent
bin inputs, older generic test calendars or malformed records to test specific
failure modes. Those controlled unit inputs are not represented as valid rows
from the new production-shaped demonstration dataset. Copied score-history
segments were re-authored independently; numerical boundary assertions remain.

Historical reports, screenshots, raw requests, old datasets/databases and
scratch archives are excluded from this candidate. New offline reports must
be generated from the replacement inputs and labeled by their actual execution
mode. No historical live result is relabeled as a new synthetic evaluation.
