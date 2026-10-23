# EXP-005 aggregate evidence — 2026-10-23

See the [experiment report](../../report.md) for commands, methodology, failures, results and limitations. Private transaction-level rows remain in ignored artifacts.

| File | Contents |
| --- | --- |
| [operations.json](operations.json) | Eight measured phases, status counts, latency/throughput, Service routing counts, cgroup snapshots/deltas, resource specs, deletion recovery, parity and cleanup |
| [cohort-audit.json](cohort-audit.json) | Counts and captured/regenerated cohort SHA-256 values; all 28,000 rows accounted for without exporting them |
| [drift.json](drift.json) | Fixed baseline bins, numeric PSI, categorical JS divergence, missingness, score/review statistics and confidence intervals |
| [quality.json](quality.json) | Oracle, eventual and four cutoff evaluations, full-cohort review budgets, reviewed/approved diagnostics, coverage/prevalence, noise audit and bootstrap intervals |
| [analysis-method.json](analysis-method.json) | Seeds, resamples, smoothing and metric definitions |
| [overview.png](overview.png) | Static nine-panel dashboard; point estimates, with intervals and denominators in JSON/report |
| [deletion-timeline.json](deletion-timeline.json) | Compact real pod/endpoint/Prometheus observations during deletion workload |
| [startup-attempts.json](startup-attempts.json) | First limited-CPU startup failure, successful bounded continuation, probe configurations and restoration |
| [final-state.json](final-state.json) | Final ready/resource/restart state and absence of temporary CPU resources |
| [validation.json](validation.json) | Tests, hashes, static checks, cleanup and runtime lineage |
| [java-tests.txt](java-tests.txt) | Actual Docker build-stage test output excerpt |
| [analysis-tests.txt](analysis-tests.txt) | Actual eight-test offline semantics output |
| [analysis-environment.txt](analysis-environment.txt) | Recorded Python 3.12 dependency versions |
| [provenance.json](provenance.json) | Delivered implementation/model hashes and capture lineage |

The initial raw run is `artifacts/EXP-005/run-20261023T2008Z` (a directory identifier; actual timestamps are recorded in the evidence). The completed continuation is `artifacts/EXP-005/run-resumed-20261023`. It copies the seven completed phases without altering the failed run and adds only the remaining limited-CPU cohort. It preserves the failure under `prior_attempt`; the final runner allowance is 360s of startup probes / 420s wait. Measurements span 20:01:30–20:14:17 UTC. The normal-CPU cohort used the original 120s probe allowance, which did not interrupt its startup or measurement; the limited-CPU measurement used the expanded allowance.

No raw features, private truth/feedback rows, credentials, kubeconfigs, image archives or transport logs are curated here. No transaction-level feature/label metric labels were added. Counts and statistics are synthetic experimental results, not production fraud estimates.
