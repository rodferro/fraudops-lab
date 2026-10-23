# EXP-005 — Production Traffic, Failure Injection, and Model Drift

## Scope and evidence

This is a bounded, local synthetic experiment on the existing EXP-004 stack. No synthetic cohort is evidence about real production fraud. The Java API, ONNX artifact, preprocessing, review threshold, Dockerfile, primary Kubernetes Deployment, and observability configuration are unchanged. Model version remains `1.0.0-exp000`; the running application image is `fraudops-api:exp004-r1`.

Curated aggregates and a static dashboard are under [evidence/20261023/](evidence/20261023/). Raw transactions, private truth labels, feedback, per-request outcomes, timelines, metrics captures, transport logs, and the Python environment remain in ignored `artifacts/EXP-005/`. The isolated kubeconfig remains `artifacts/EXP-003/kubeconfig`; every Kubernetes command specifies context `kind-fraudops-exp003` and namespace `fraudops-exp003`. No default context or external service was changed.

## Reproduction and cleanup

Prerequisites: running Docker/kind cluster and EXP-004 stack, `kubectl`, Python 3.12, and free loopback ports 18081, 18083, 19090. A new cluster can be provisioned with the existing [EXP-003](../EXP-003/report.md) and [EXP-004](../EXP-004/report.md) instructions; this experiment does not recreate it. Start with exactly three ready API replicas, the independent `fraudops-client` pod, and Prometheus. Stop manual forwards using these ports before running.

```bash
# Recorded host bootstrap; use your Python 3.12 path on another machine.
PYTHON_BOOTSTRAP=/opt/homebrew/bin/python3.12 bash scripts/exp005.sh setup
bash scripts/exp005.sh test

RUN="artifacts/EXP-005/run-$(date -u +%Y%m%dT%H%M%SZ)"
bash scripts/exp005.sh run "$RUN"
bash scripts/exp005.sh analyze "$RUN" "$RUN-aggregates"
# Review aggregates before promoting them to experiments/EXP-005/evidence/.

# Existing Java / ONNX parity suite, through the available build stage:
docker build --target build --no-cache-filter build -t fraudops-exp005-tests:local .

# After an interrupted runner, stop that runner first (and its own port-forwards).
bash scripts/exp005.sh cleanup
# Optional teardown of the entire dedicated learning environment:
# bash scripts/exp003.sh cleanup
```

`setup` installs [separately pinned analysis dependencies](../../scripts/exp005-requirements.txt), without touching the training requirements or retraining. The runner creates a **new** output directory, sends finite cohorts, closes its own forwards, restores the temporary replica's CPU template to 1, and deletes that temporary Deployment in `finally`. The original API spec is compared before/after. `cleanup` deletes only `fraudops-exp005-cpu`; it leaves the primary API and observability stack running. After SIGKILL or a machine interruption, the temporary replica may remain idle until cleanup; there is no autonomous stress loop or background traffic Job.

Each traffic phase stops admission after 180 seconds, has at most `2 × concurrency` outstanding tasks, at most `concurrency` active requests, and a five-second HTTP timeout. Already admitted work drains within its request timeouts. Unsent rows after a deadline are retained as explicit failures. Requests have no retries. The temporary clone's cold-start wait is bounded at 420 seconds and its startup probe allowance at approximately 360 seconds. All counts and seeds are fixed in [exp005_data.py](../../scripts/exp005_data.py).

## Simulator and clocks

[exp005_data.py](../../scripts/exp005_data.py) mirrors the distributions, original log-odds formula, merchant effects, and missingness probabilities from `train.generate_dataset`. It keeps latent values until truth is sampled, then applies missingness. It does not call training or regenerate the accepted CSV/model. Event times use a deterministic one-hour synthetic cohort on separate January 2026 days, rather than EXP-000's year-long timestamp sort. Distinct seeds make baseline and burst independent samples from the same population, rather than duplicate rows.

| Phase | Seed | Transactions | Offered requests/s | Active concurrency |
| --- | ---: | ---: | ---: | ---: |
| Baseline | 5001 | 5,000 | 100 | 16 |
| Burst | 5002 | 5,000 | 500 | 32 |
| Post-burst recovery | 5003 | 2,000 | 100 | 16 |
| Covariate drift | 5004 | 5,000 | 100 | 16 |
| Concept drift | 5005 | 5,000 | 100 | 16 |
| Pod deletion | 5006 | 2,000 | 50 | 16 |
| CPU normal / limited | 5007 each | 2,000 each | 150 each | 16 each |

Baseline/burst use channel probabilities `[.43, .36, .21]`, merchant probabilities `[.34, .18, .18, .12, .18]`, lognormal amount `(3.9, .9)`, uniform account age 1–4000, Poisson(2) recent count, exponential distance mean 15 and device age mean 240, country mismatch probability .035, and 97% card presence conditional on in-store channel.

Covariate drift multiplies amount by 3, distance by 6, and device age by .15; changes channel probabilities to `[.15, .15, .70]`; regenerates card presence consistently; and raises missingness for amount to 2% and distance/device age to 8%. Labels are resampled from the **same original latent conditional risk formula** using the changed values. Thus true prevalence can change without changing that conditional mechanism. Missingness is a separate observation-process shift; conditioning on observed/imputed values need not preserve the latent relationship exactly.

Concept drift retains the baseline feature and missingness recipe. Starting from the original log-odds, it adds `4.2 - .8*log1p(amount) - 1.8*country_mismatch - 1.44*(1-card_present) - .8*(device_age<30)`. This reverses the amount, mismatch, absent-card, and new-device coefficients. The fixed 4.2 offset was declared in code before API evaluation; it was not fitted to scores or feedback. Other relationships remain unchanged. A paired-seed test confirms baseline/concept feature arrays are identical when explicitly given the same seed; the measured independent cohorts use distinct seeds.

Every raw row has a unique ID, scenario, deterministic event timestamp, the nine features, simulator-only truth, request scheduling/start/finish times, HTTP/transport status, latency, and—on valid success—API score, decision, and model version. Payload serialization sends **only the nine-feature dictionary**. Private truth and feedback never enter the API or Prometheus labels. The request ID is carried in the existing access-log header for routing attribution.

Four clocks are deliberately separate: synthetic transaction event time, real request wall time, simulated feedback arrival time, and simulated evaluation cutoff. Absolute arrival scheduling and bounded backpressure record schedule lag separately from HTTP wire latency. Achieved throughput counts successful responses over actual measurement duration, including drain. Deadline/non-HTTP failures remain in attempted totals; model quality excludes unscored failures and reports that denominator explicitly.

## Operational methodology

The main path is local Python → loopback `kubectl port-forward pod/fraudops-client 18081:8081` → independent Java bridge → `http://fraudops-api:8080` **ClusterIP** → healthy API endpoint. The bridge uses eight workers, fresh upstream connections, a 250m CPU / 192Mi limit, and a three-second upstream read timeout. It can bottleneck a test. Request IDs in per-pod Tomcat logs provide routing evidence; a Service port-forward alone would select a single pod and would not demonstrate balancing.

The API keeps requests 250m / 256Mi and limits 1 CPU / 512Mi per pod. Prometheus scrapes ready endpoints every two seconds. Spring HTTP and ONNX wrapper quantiles aggregate histogram buckets using `increase` over the phase window. These are interpolated estimates with scrape-window uncertainty and bucket resolution, not exact timer samples or exact request counts; the expanded window can include boundary activity. In particular, sub-millisecond HTTP estimates interpolate the lowest bucket. Client percentiles are empirical measured latencies and include forwarding, networking, queues, HTTP, and inference; ONNX wrapper latency covers the instrumented delegate call only.

During pod deletion, an observer records pod UIDs/readiness, EndpointSlices, and active Prometheus targets about once per second **plus command time**. Deletion occurs after approximately eight seconds of active traffic. Replacement and recovery times are first-observation bounds measured from immediately before the deletion command, not Kubernetes event timestamps with sub-second precision. The traffic path never forwards directly to the deleted API pod. Requests are also summarized over the interval from deletion to observed readiness restoration. Successful finite probes demonstrate no observed errors in that workload; they do not establish continuous or production availability.

CPU comparison uses a separate one-replica Deployment with the same image, model, ConfigMap, security, and memory limit. Its label `app=fraudops-exp005-cpu` excludes it from the main Service. CPU request is 50m; limits are 1 CPU and 100m. Trace export is disabled in **both** comparison cases. Both use identical seed-5007 feature/label arrays, the same 100 fixture warmup requests, arrival rate and concurrency, and a direct loopback pod-forward on port 18083. They are comparable to each other, not directly to the bridge's latency or three-pod capacity. Different JVMs and one run per setting leave startup/JIT and host variability unresolved.

The clone is outside the existing Prometheus Service allowlist. Instead of changing discovery, before/after `/actuator/prometheus` snapshots on the same pod supply exact counter differences and interpolated HTTP/ONNX histogram quantiles, excluding warmup. cgroup v2 `cpu.stat`, `memory.current`, and lifetime `memory.peak` are captured before/after phases. CPU deltas require matching pod UID; replacement pods never inherit another pod's counters. Throttling is `Δnr_throttled / Δnr_periods`; throttled microseconds are retained separately. Approximate CPU core equivalents divide usage delta by client phase duration; snapshot collection overhead makes these approximate. Memory peaks are container-lifetime values, not isolated phase peaks. No Metrics Server/cAdvisor utilization series is assumed.

## Drift and model-quality methodology

[exp005_analyze.py](../../scripts/exp005_analyze.py) fits numeric decile boundaries on the measured baseline only, merges duplicate quantiles, and extends outer boundaries to ±infinity. Bins stay fixed for every scenario and bootstrap sample. PSI uses `(q-p)*ln(q/p)` summed across bins with **0.5 added to every count before normalization**. Numeric PSI excludes missing values; missing rates and their deltas are reported for all nine features. Binary country/card features are treated as categorical alongside channel/merchant codes. Categories have fixed known codes plus explicit missing/unknown bins. Jensen–Shannon divergence uses base-2 logs, in bits, and the same smoothing. It is divergence, rather than the square-root distance returned by [SciPy's `jensenshannon`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.spatial.distance.jensenshannon.html).

Scores use separately locked baseline score bins, PSI, quantiles, mean, review count/rate, and approval rate. Sample sizes accompany results. PSI is descriptive; customary small/moderate/large PSI cutoffs are heuristics, not evidence that a classifier has failed. Marginal drift cannot detect every conditional/concept change, and distance drift need not imply model sensitivity to distance.

There are 200 iid row bootstrap resamples with seed 5100 and 95% percentile intervals. Drift resamples baseline/scenario cohorts independently while holding bins fixed; baseline self-comparison is exactly zero and has no independent-comparison interval. Quality resamples whole scored rows with their truth, feedback, decision and availability together. The same resampled indices are used across that cohort's evaluation modes. Top-budget policies are recalculated on the entire resampled cohort. Undefined resamples are excluded and their valid count is reported. Intervals describe finite synthetic-cohort sampling uncertainty, conditional on these bins/recipes; they are not p-values and do not correct selection bias, noisy labels, misspecification, or real temporal dependence. Early cutoffs have very few positives and wide or undefined intervals. Zero-capture bootstrap intervals can be degenerate because resampling cannot invent unseen cases; they do not establish zero population capture or risk.

Feedback RNG seed is scenario seed + 100000. In baseline/burst/covariate, eventual observation probability is .90. Fraud feedback delay is lognormal with median 48 hours and log-sigma .8; nonfraud median is 6 hours. Observed labels independently flip with false-negative probability .15 or false-positive probability .01, based on **truth**. Missing feedback has neither label nor arrival time. “Eventual” includes arbitrarily late observed feedback and is an offline upper-horizon comparison, not an immediately available signal.

Concept drift uses a deliberately selected policy: review decisions have .95 observation probability and delay multiplier .25; approved transactions have .15 observation probability and delay multiplier 3. Truth-specific median delays still apply. Evaluation cutoffs are 1, 12, 48, and 168 hours after the last simulated event. Persisted availability flags are checked against recomputed arrival times. Cutoff evaluations mask labels until arrival and never substitute truth or eventual labels for unavailable feedback.

Each scenario compares simulator **oracle truth**, all eventual noisy feedback, and each cutoff's available noisy feedback. ROC-AUC and PR-AUC are null when fewer than two classes exist, including all-negative subsets. PR-AUC here means non-interpolated [scikit-learn average precision](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.average_precision_score.html), rather than trapezoidal PR area. Precision is TP / reviewed labeled transactions; recall is TP / positive labels in that evaluation subset. Zero denominators are null. Fixed-threshold review volume is separately counted over the entire scored cohort, so missing feedback does not silently change operational volume.

For 1%, 2%, and 5% review budgets, select `ceil(budget × entire scored cohort N)` highest scores before applying feedback masks, with stable row/ID order for ties. Capture is known positives in that selection / all positive labels available in the evaluation subset. Precision uses known selected labels as denominator, with the number of selected-but-unlabeled rows exposed. Cutoff capture is therefore a feedback-selected estimate, not population fraud capture. Reviewed/approved group metrics use their own label-coverage and prevalence denominators; ROC/AP are conditional diagnostics and may be null in small or single-class groups. They must not be compared as interchangeable population estimates.

## Actual results

The captured session ran **2026-10-23T20:01:30.981551+00:00 → 2026-10-23T20:14:17.334043+00:00**, including the failed startup attempt and its resumed CPU phase. All **28,000 cohort requests** completed HTTP 200 with valid score/decision/model-version responses: **24,000 through the actual ClusterIP Service**, plus 4,000 through the isolated CPU replica. No request was dropped, left undispatched, retried, or excluded from core quality analysis. These counts exclude fixture checks and warmup. All eight cohort fingerprints match independent regeneration; [cohort audit](evidence/20261023/cohort-audit.json) contains counts and SHA-256 values without private rows.

### Traffic, recovery and CPU quotas

| Phase | Success / attempted | Achieved rps | Client p95 ms | HTTP p95 ms* | ONNX p95 ms* |
| --- | --- | --- | --- | --- | --- |
| baseline | 5000 / 5000 | 100.01 | 4.031 | 0.981 | 0.148 |
| burst | 5000 / 5000 | 499.95 | 6.304 | 0.958 | 0.107 |
| recovery | 2000 / 2000 | 100.03 | 3.768 | 0.952 | 0.107 |
| covariate | 5000 / 5000 | 100.01 | 3.869 | 0.956 | 0.109 |
| concept | 5000 / 5000 | 100.01 | 3.409 | 0.953 | 0.104 |
| deletion | 2000 / 2000 | 50.02 | 5.051 | 1.164 | 0.154 |
| cpu_normal | 2000 / 2000 | 150.02 | 3.367 | 0.966 | 0.103 |
| cpu_limited | 2000 / 2000 | 15.69 | 1998.572 | 1229.063 | 341.431 |

*Server columns are histogram estimates. Main phases use Prometheus windows; CPU phases use same-pod counter deltas. Exact client counts are authoritative; Prometheus extrapolated increases (for example baseline ≈5,117.9 versus 5,000 client successes) are not substituted for them.

Burst offered load was achieved, and recovery latency returned near baseline. API burst CPU use was approximately 0.20–0.22 core per pod. The bridge used approximately 0.103 core and had 4/101 throttled periods during burst; the busiest API pod had 4/102. There is no measured throughput collapse or proof of a bridge capacity ceiling at this offered rate. The client/server latency gap includes the entire forwarding path; its components were not separately profiled.

Actual baseline access-log attribution was **`fraudops-api-757c4c856b-dlcjc`: 1,701, `fraudops-api-757c4c856b-pfq2s`: 1,634, `fraudops-api-757c4c856b-qrc2r`: 1,665** (sum 5,000). This demonstrates Service routing to all three endpoints. Burst attribution also sums to 5,000 across the same three pods. During deletion, logs from the removed pod are unavailable after termination; its pre-delete capture is a lower bound, so a complete post-delete per-pod split is not claimed.

Deleting `fraudops-api-757c4c856b-dlcjc` produced a first-observed replacement after **0.81s**, three ready replicas after **5.25s**, and matching healthy Prometheus targets after **10.85s**. The observer captured four samples with two ready pods. **263 requests** began during readiness recovery, with **0 errors**, p95 **6.67ms**, and p99 **12.92ms**. All 2,000 requests across the full deletion phase succeeded. This is no observed request downtime at the sampled load, not proof that no instant of unavailability existed. [Compact timeline](evidence/20261023/deletion-timeline.json) retains pod, endpoint and target observations.

| CPU case | Δ periods | Δ throttled periods | Throttled periods | Δ throttled µs | Approx. cores | Client schedule-lag p95 |
| --- | --- | --- | --- | --- | --- | --- |
| cpu_normal | 135 | 0 | 0.00% | 0 | 0.221 | 0.00 s |
| cpu_limited | 1299 | 1292 | 99.46% | 673457346 | 0.099 | 108.84 s |

The constrained workload took **127.49s**, versus **13.33s** at 1 CPU, and retained every attempted request. Offered 150 rps substantially exceeded achieved 15.69 rps: the bounded client queue applied backpressure, with schedule-lag p95 **108.84s**. That lag is separate from the measured HTTP p95 ≈2.00s. Zero errors here coexist with severe latency and arrival-schedule failure; this is not evidence of acceptable service performance.

The ONNX wrapper p95 under quota was **341.43ms** versus **0.103ms** at 1 CPU. This is elapsed wall time around the delegate and includes scheduling/throttling pauses; it does not isolate native arithmetic cost. Likewise, 99.46% of periods being throttled is not 99.46% downtime, and the throttled-time counter is not used as a wall-time outage or CPU-utilization percentage. HTTP and ONNX counter differences both equal exactly 2,000 in each CPU case.

### Failed cold start and bounded continuation

The initial temporary 100m replica exhausted the inherited 60 × 2s startup-probe allowance. Kubernetes recorded `Unhealthy` and `Killing` (startup-probe restart ordered), and the runner timed out at 150s before starting its measured limited-CPU cohort. Its previously measured normal-CPU and six Service phases remain intact. The main API was unaffected. The captured first-attempt container status still showed restart count 0 before the ordered restart; a completed restart count was not captured.

The temporary clone alone received a 180 × 2s allowance and a 420s bounded wait. A new output directory reused the seven completed phases after checking the original API spec and model/metadata hashes; it neither overwrote the failed run nor repeated its traffic. The resumed 100m container started at **20:09:00Z** and became Ready at **20:11:39Z** (**159s**), with restart count 0. After identical warmup it completed the remaining 2,000-request phase. The helper now uses the longer startup allowance for fresh reproductions. [Startup attempts](evidence/20261023/startup-attempts.json) preserves both configurations, status and failure events.

Actual continuation command (run from repository root):

```bash
artifacts/EXP-005/venv/bin/python scripts/exp005_run.py \
  --reuse-traffic artifacts/EXP-005/run-20261023T2008Z \
  --output artifacts/EXP-005/run-resumed-20261023
```

All compared steady-state pods and the final three main pods show restart count 0, Ready true, and no recorded `OOMKilled` state. No OOM-related event appears in the captured experiment events. Main API memory snapshots were approximately 264–267MiB around baseline/burst; the limited clone ended at 222.51MiB with lifetime peak 229.89MiB, below its 512MiB limit. These snapshots do not establish absence of memory pressure between observations. The clone CPU template was restored to 1 before deletion; final cluster capture confirms no CPU-test pods/Deployment remain, three ready main pods, and the original API spec unchanged. [Operations](evidence/20261023/operations.json) retains resource counters and [final state](evidence/20261023/final-state.json) retains verification.

### Feature, score and review drift

| Scenario (N=5,000) | Amount PSI | Distance PSI | Device-age PSI | Channel JS bits | Score PSI | Review count / rate | True prevalence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 0.000 | 0.000 | 0.000 | 0.0000 | 0.000 | 50 / 1.00% | 1.86% |
| burst | 0.002 | 0.002 | 0.005 | 0.0002 | 0.004 | 39 / 0.78% | 2.10% |
| covariate | 1.314 | 1.894 | 3.639 | 0.1716 | 1.525 | 1022 / 20.44% | 4.22% |
| concept | 0.005 | 0.001 | 0.002 | 0.0001 | 0.003 | 59 / 1.18% | 2.00% |

Covariate PSI 95% intervals were **[1.230, 1.425]** for amount, **[1.785, 2.022]** for distance and **[3.477, 3.819]** for device age. Score PSI was **1.525 [1.435, 1.645]**. Review volume rose from **1.00% [0.76%, 1.26%]** to **20.44% [19.56%, 21.44%]**, despite successful API requests. Observed missingness changed from **0.50% → 1.82%** for amount, **1.90% → 7.22%** for distance, and **2.12% → 8.78%** for device age; [drift JSON](evidence/20261023/drift.json) records every feature rate/delta and interval.

Concept drift kept small measured marginal distances (numeric PSI ≤0.0048; score PSI **0.0027**) and a review rate of **1.18%**, yet oracle ROC-AUC fell from **0.662 [0.614, 0.706]** to **0.497 [0.443, 0.556]** and all top-5%-ranked transactions were nonfraud in this realization. This is an explicit example of conditional degradation that marginal/score monitoring misses. Covariate average precision increased with its higher fraud prevalence and changed population; that increase alone is not an improvement claim.

### Oracle truth versus delayed/noisy feedback

| Scenario | Label source | Labeled N | Coverage | Label prevalence | ROC-AUC | AP | Threshold precision | Threshold recall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | oracle | 5000 | 100.00% | 1.86% | 0.662 | 0.034 | 4.00% | 2.15% |
| baseline | eventual | 4457 | 89.14% | 2.67% | 0.575 | 0.036 | 2.08% | 0.84% |
| baseline | 12h | 3563 | 71.26% | 1.29% | 0.493 | 0.014 | 0.00% | 0.00% |
| burst | oracle | 5000 | 100.00% | 2.10% | 0.648 | 0.046 | 10.26% | 3.81% |
| burst | eventual | 4488 | 89.76% | 2.81% | 0.605 | 0.046 | 7.89% | 2.38% |
| burst | 12h | 3628 | 72.56% | 1.05% | 0.529 | 0.012 | 0.00% | 0.00% |
| covariate | oracle | 5000 | 100.00% | 4.22% | 0.634 | 0.077 | 7.05% | 34.12% |
| covariate | eventual | 4504 | 90.08% | 4.44% | 0.585 | 0.068 | 6.30% | 29.00% |
| covariate | 12h | 3543 | 70.86% | 1.13% | 0.536 | 0.014 | 1.43% | 25.00% |
| concept | oracle | 5000 | 100.00% | 2.00% | 0.497 | 0.020 | 0.00% | 0.00% |
| concept | eventual | 819 | 16.38% | 3.17% | 0.510 | 0.032 | 0.00% | 0.00% |
| concept | 12h | 289 | 5.78% | 0.69% | 0.564 | 0.013 | 0.00% | 0.00% |

Availability at all simulated cutoffs (hours after cohort end):

| Scenario | 1h | 12h | 48h | 168h | Eventual |
| --- | --- | --- | --- | --- | --- |
| baseline | 3.62% | 71.26% | 87.88% | 88.94% | 89.14% |
| burst | 3.78% | 72.56% | 88.34% | 89.70% | 89.76% |
| covariate | 3.92% | 70.86% | 87.80% | 89.66% | 90.08% |
| concept | 0.52% | 5.78% | 14.66% | 16.22% | 16.38% |

The concept 1h subset has **26 labels and zero positives**: ROC-AUC/AP and recall are explicitly undefined. Its 12h subset has only **two positives**, so its apparent ROC-AUC 0.564 has substantial uncertainty. No future feedback is borrowed to fill those gaps. Even 168h differs from eventual: baseline has 4,447 labels at 168h versus 4,457 eventually.

Oracle capture of all true fraud at fixed whole-cohort review budgets (50 / 100 / 250 transactions):

| Scenario | 1% capture | 2% capture | 5% capture |
| --- | --- | --- | --- |
| baseline | 2.15% | 4.30% | 12.90% |
| burst | 4.76% | 7.62% | 14.29% |
| covariate | 2.84% | 5.69% | 14.22% |
| concept | 0.00% | 0.00% | 0.00% |

For comparison, baseline 1%-budget capture is **2/93 = 2.15%** with oracle labels, but **1/119 = 0.84%** with eventual noisy labels; only **48/50** selected transactions have eventual feedback. The policy still selected the same 50 transactions. All budgets, selected-known denominators and bootstrap intervals for every cutoff/group are in [quality JSON](evidence/20261023/quality.json).

Noise and selection are visible in the private-oracle audit: baseline eventual feedback contains **54 false positives and 14 false negatives**, moving apparent prevalence from 1.86% true to 2.67% observed. In concept drift, reviewed eventual coverage is **53/59 = 89.83%**, while approved coverage is **766/4,941 = 15.50%**. The 59 reviewed transactions contain **zero true fraud**; their oracle ROC/AP are undefined, while approved transactions contain all 100 true fraud cases. Eventual observed labels contain 26 positives across 819 labeled transactions (3.17%), versus 100/5,000 true fraud (2.00%). Early and eventual metrics therefore describe different, selected/noisy populations—not a repaired estimate of full-cohort quality. Full reviewed/approved conditional diagnostics remain available, including null metrics rather than invented values.


## Monitoring interpretation and limitations

Immediate operational signals: HTTP/transport errors, achieved throughput versus offered rate, client schedule lag, ready replicas, endpoint/target counts, HTTP and ONNX histograms, JVM/container memory, restart/OOM state, and CPU throttling. CPU limits can throttle execution; requests and limits serve different scheduling/enforcement roles ([Kubernetes resource documentation](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/)). Correlate these signals before attributing high client latency to inference.

Immediate unlabeled signals: feature PSI/JS, missingness, score distributions and review volume, always accompanied by cohort sizes, baseline definition and bin policy. Large review-volume changes can matter operationally even with zero HTTP errors. Stable marginals and scores cannot establish unchanged conditional quality.

Delayed signals: available-label coverage, class-specific/decision-specific coverage when auditable, observed prevalence, ROC/AP, threshold precision/recall and budget capture. In a real system truth-dependent coverage is usually unknown; it is visible here only because the simulator owns the oracle. Delay preferentially hides fraud early; missingness excludes some events forever; noisy labels can substantially change rare-event prevalence; review selection changes which parts of the score distribution are labeled. Longer waiting cannot repair missing/noisy/selected feedback. Confidence intervals cannot remove those biases. This experiment does not add production alerts or attempt propensity correction, retraining, or a replacement model.

The static [overview](evidence/20261023/overview.png) combines operational timing/throttling, input drift, review volume, oracle-versus-feedback quality, and label availability. Full score drift, capture budgets, uncertainty, and reviewed/approved metrics are in JSON. Transaction-level features or labels are never exported as metric labels or committed evidence.

These are finite, independent synthetic cohorts, one local kind node, one host session and one run per setting, with a shared Docker VM and observability overhead. No production capacity, sustained stress limit, causal effect size, real fraud mechanism, node failure resilience, or business loss estimate is established. Prometheus/Jaeger retain ephemeral bounded history; committed aggregates preserve this run independently. The temporary clone's cold-start failure is a measured resource/probe interaction, distinct from steady-state inference performance.

## Validation and acceptance

The completed, resumed evidence satisfies all eight EXP-005 acceptance criteria. The final helper was not rerun from scratch after the startup-budget change: seven completed phases were preserved, and the modified limited-CPU startup/measurement was runtime-verified separately. Fingerprint checks confirm all eight captured cohorts match the delivered simulator. No runtime dependency remains blocked.

| Check | Result |
| --- | --- |
| Four deterministic core scenarios through ClusterIP | Passed; 5,000 scored transactions per scenario, independent seeds and matching regeneration hashes |
| Burst and post-burst recovery | Passed; achieved rates, client timing, server histograms, bridge/API cgroups and per-pod attribution captured |
| Pod deletion under active traffic | Passed; ready/endpoint/Prometheus timeline, recovery timing and request outcomes captured |
| CPU pressure and restoration | Passed after documented startup failure/retry; 1 CPU versus 100m, quota counters, HTTP/ONNX timing, throughput and failures captured; clone removed |
| Drift and delayed/noisy/selected feedback | Passed; numeric PSI, categorical JS, missingness, scores/reviews, oracle/eventual/cutoff metrics, conditional groups and bootstrap intervals retained |
| Existing Java tests / ONNX parity | 16 tests, zero failures/errors/skips through a fresh Docker build stage; live five-case parity before/after also passed |
| Offline experiment semantics | 8 tests passed: deterministic cohorts, paired concept/CPU inputs, private feature contract, time masks/no future leakage, full-cohort budget selection, undefined metrics, drift smoothing/tails, and histogram deltas |
| Static validation and preservation | Bash/Python syntax, all internal Markdown file targets, all three Kustomize entry points, runtime/kubeconfig ignore rules, curated inclusion, and `git diff --check` passed; model and metadata SHA-256 unchanged |
| Final environment | Three main API replicas Ready with zero restarts; original Deployment spec unchanged; temporary CPU resources absent and owned forward ports closed |

[Validation JSON](evidence/20261023/validation.json), [Java test output](evidence/20261023/java-tests.txt), and [analysis test output](evidence/20261023/analysis-tests.txt) record checks. ONNX SHA-256 remains `60af5e22073af550a5bfb249d4590afba12d3529221deb3d8e83b46eaa71a757`; metadata SHA-256 remains `747298910af4e0c230821a0406381b48c95b4d8edb1aca41b87a834359fe0068`.

Two auxiliary tooling issues were resolved without changing measurements: host Python 3.9's compile check attempted an external cache write and was denied, so compilation used the local Python 3.12 venv; initial font-cache warnings did not prevent rendering, and the helper now gives Matplotlib/fontconfig ignored writable cache paths. The generated overview was visually inspected. No Java, model, Docker, existing Kubernetes/observability source, accepted CSV, or prior experiment evidence was changed.
