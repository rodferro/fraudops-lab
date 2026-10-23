# EXP-004 — FraudOps Observability

## Result

**Runtime acceptance passed** on 2026-10-23. The final complete trial ran **16:15:22–16:17:45 America/Sao_Paulo** (19:15:22–19:17:45 UTC). Prometheus scraped all three ready API pods; Grafana returned working HTTP and ONNX histogram data; Jaeger returned HTTP server spans with explicit ONNX children; replacement and a real image rollout were observable; both demonstration alerts reached `firing` and subsequently resolved. All 15 existing Java tests plus one new observability integration test passed. Model version remains `1.0.0-exp000`.

The instrumented API, four observability components, manifests, dashboard, and helpers are implemented and statically validated. The requested runtime scenarios are verified. This remains a local, single-node learning experiment with light recovery/rollout traffic and a short load trial.

Primary evidence: [complete run summary](evidence/runtime-20261023/summary.json), [derived analysis](evidence/runtime-20261023/analysis.json), [prediction outcomes](evidence/runtime-20261023/requests.jsonl), [client load results](evidence/runtime-20261023/load-client.json), and [firing alert API response](evidence/runtime-20261023/alerts-firing.json). There were two earlier incomplete verifier attempts, described below; their results are not substituted for the successful trial.

## Implementation and privacy

Spring Boot remains at 3.5.8 / Java 21. Its dependency management resolves Micrometer Prometheus registry 1.15.6, Micrometer Tracing/OTel bridge 1.5.6, and OpenTelemetry SDK/exporter 1.49.0. [Packaged dependencies](evidence/jar-contents.txt).

The three added dependencies are `micrometer-registry-prometheus`, `micrometer-tracing-bridge-otel`, and `opentelemetry-exporter-otlp`. Boot supplies HTTP server observation and tracing; there is no Java agent or second HTTP instrumentation starter. This uses Boot's [documented Micrometer/OTLP integration](https://docs.spring.io/spring-boot/3.5/reference/actuator/tracing.html).

[InstrumentedFraudModel](../../src/main/java/com/fraudops/inference/InstrumentedFraudModel.java) is the primary `FraudModel` bean and delegates to the unchanged `OnnxFraudModel`. It records attempts and duration, and starts an explicit `onnx.inference` span under the current HTTP span. The duration includes tensor creation, `OrtSession.run`, result extraction, and cleanup; it is not an isolated measurement of the native `run` call. Success and error counters are registered at zero before traffic. Exceptions increment the error outcome even when an exception handler returns HTTP 500. The original exception still propagates to the existing handler; the span receives a sanitized constant error message.

The controller counts successful approve/review decisions and logs only the event, decision, and model version. The console format includes `image`, `trace_id`, and `span_id`. No features, transaction amounts, probabilities, account identifiers, or request bodies are attached to logs or custom spans.

| Prometheus metric | Meaning and bounded application labels |
| --- | --- |
| `http_server_requests_seconds_count` | Completed HTTP requests; framework method, templated URI, status, outcome, exception/error labels |
| `http_server_requests_seconds_bucket` / `_sum` | HTTP duration histogram in seconds; same labels plus `le` |
| `fraud_inference_total{outcome="success\|error"}` | Attempts entering the model wrapper, including failures; invalid HTTP input rejected before inference is excluded |
| `fraud_onnx_inference_duration_seconds_bucket` / `_count` / `_sum` | Model-call duration in seconds; histogram `le` only |
| `fraud_decisions_total{decision="approve\|review"}` | Successful prediction decisions |
| `jvm_memory_used_bytes`, `jvm_gc_pause_seconds_*`, `jvm_threads_live_threads` | Boot/Micrometer JVM bindings; heap pools, collectors and states have bounded names |
| `process_cpu_usage` and other available process metrics | JVM-reported process measurements, not Kubernetes resource utilization |

The table's `success|error` and `approve|review` denote alternative literal label values. Prometheus adds Kubernetes `app`, `pod`, `namespace`, `instance`, and `job` labels during discovery. Pod churn creates new historical series within the short retention period; application data never creates a label value. A [post-run label inspection](evidence/runtime-20261023/metric-label-review.json) reviewed 7,915 stored series and found no forbidden transaction/request/trace identifier label names. JVM labels such as `id` describe memory pools, and filesystem `path` describes a fixed filesystem location.

Both parity artifacts remain unchanged: ONNX SHA-256 `60af5e22073af550a5bfb249d4590afba12d3529221deb3d8e83b46eaa71a757`; metadata SHA-256 `747298910af4e0c230821a0406381b48c95b4d8edb1aca41b87a834359fe0068`. No decision-threshold or model/preprocessing changes were made.

## Stack configuration

The existing isolated kind cluster and namespace `fraudops-exp003` were reused. Docker Desktop Engine 29.9.2 supplied 10 CPUs and approximately 8 GiB to its Linux ARM64 VM. kind v0.33.0 runs Kubernetes v1.37.0; kubectl is v1.37.1. All new images were Linux ARM64. [Image identities](evidence/images.txt), [Kubernetes versions](evidence/runtime-20261023/versions.json).

| Component | Explicit image | Configuration / resources |
| --- | --- | --- |
| API | `fraudops-api:exp004`, then `exp004-r1` | 3 replicas, each requests 250m / 256Mi and limits 1 CPU / 512Mi; existing probes/security/shutdown settings preserved |
| Prometheus | `prom/prometheus:v3.2.1` | Scrape and rule evaluation every 2s, scrape timeout 1s; requests 100m / 128Mi, limits 1 CPU / 512Mi |
| Grafana | `grafana/grafana:11.6.0` | Provisioned Prometheus + Jaeger sources and dashboard; requests 100m / 128Mi, limits 1 CPU / 384Mi |
| OTel Collector | `otel/opentelemetry-collector:0.123.0` | OTLP HTTP receiver on 4318, memory limiter and batch processor, OTLP gRPC export to Jaeger:4317; requests 50m / 64Mi, limits 500m / 192Mi |
| Jaeger | `jaegertracing/all-in-one:1.67.0` | Query/UI on 16686, OTLP gRPC on 4317, memory storage capped at 10,000 traces; requests 50m / 96Mi, limits 500m / 384Mi |

Manifests and configuration are under [k8s/observability/](../../k8s/observability/). Standard Deployments, Services, ConfigMaps, a namespace-scoped discovery Role/RoleBinding, and a temporary demo Job are used. Prometheus alone receives the namespace discovery token. Shared resources moved to `k8s/base/` so overlays can reuse them without a Kustomize parent-directory cycle; `kubectl apply -k k8s/` still deploys EXP-003's one-replica base.

Prometheus discovers `discovery.k8s.io/v1` EndpointSlices in this namespace, keeps only the inference/demo Services, named HTTP ports, and ready endpoints, then scrapes pod IPs directly. It does not scrape a port-forward or the ClusterIP as a single target. Ready-state filtering uses the [documented EndpointSlice discovery labels](https://prometheus.io/docs/prometheus/latest/configuration/configuration/#kubernetes_sd_config). Kubernetes readiness and Prometheus target health can change at different times as metadata propagates and a first scrape completes.

All Services are ClusterIP. User interfaces are reachable through loopback port-forwards; there is no ingress, external account, or external credential. Grafana permits anonymous local Viewer access, disables its login form and signup, and has no editable provisioned dashboard. The stack uses read-only container roots and ephemeral storage. Prometheus retains at most two hours of samples with a 512MB retention-size setting and 768Mi volume bound; Jaeger is entirely in memory. These settings do not provide durable retention, authentication for public access, or production resilience.

Tracing export defaults off outside the experiment. The overlay sets `FRAUD_TRACE_EXPORT_ENABLED=true`, `FRAUD_OTLP_ENDPOINT=http://otel-collector:4318/v1/traces`, and sampling probability 1.0 for this short trial. The application's ordinary default probability is 0.1. Collector pipelines are defined in [collector.yml](../../k8s/observability/config/collector.yml), using its [receiver/processor/exporter configuration model](https://opentelemetry.io/docs/collector/configuration/).

## Reproduction and access

Prerequisites: the EXP-003 isolated cluster, its local base/client images and running client pod, Docker with host-platform image export support, kind, kubectl, Bash, and Python 3.9+. Host Java/Maven are not required. For a fresh environment, first run `bash scripts/exp003.sh create`, `images`, and `deploy`.

```bash
bash scripts/exp004.sh build
bash scripts/exp004.sh images
bash scripts/exp004.sh deploy
bash scripts/exp004.sh verify
# Recorded output was requested explicitly:
# bash scripts/exp004.sh verify experiments/EXP-004/evidence/runtime-20261023
python3 scripts/exp004_analyze.py experiments/EXP-004/evidence/runtime-20261023
```

`build` runs the existing Dockerfile's `mvn clean verify` and derives an image revision. `images` downloads the four versioned components, exports only `linux/$(docker version --format '{{.Server.Arch}}')`, and loads the archive into kind, including both local API images. No application registry is needed. The default user Kubernetes context is never selected or modified: commands use `artifacts/EXP-003/kubeconfig` and explicit `kind-fraudops-exp003` context.

The verifier requires free ports 18080/18081/18082/19090/13000/16686, starts and stops its own port-forwards, uses a new output directory, deletes one API pod, and changes the Deployment image to `exp004-r1`. Run `deploy` before repeating verification to restore three `exp004` replicas. The initial check explicitly waits for old terminating pods to disappear, in addition to Deployment rollout success.

The EXP-003 client bridge sends fresh upstream connections to the actual `fraudops-api:8080` ClusterIP, without retries. Load and recovery measurements therefore include the local client, API-server port-forward, bridge HTTP server, and Service routing. The bridge has eight worker threads. An ordinary Service port-forward selects one pod and cannot demonstrate traffic distribution or survive deletion of its selected pod by itself.

Open each interface in a separate terminal:

```bash
export KUBECONFIG="$PWD/artifacts/EXP-003/kubeconfig"
kubectl --context kind-fraudops-exp003 -n fraudops-exp003 port-forward --address=127.0.0.1 service/grafana 13000:3000
kubectl --context kind-fraudops-exp003 -n fraudops-exp003 port-forward --address=127.0.0.1 service/prometheus 19090:9090
kubectl --context kind-fraudops-exp003 -n fraudops-exp003 port-forward --address=127.0.0.1 service/jaeger 16686:16686
```

Grafana: `http://127.0.0.1:13000/d/fraudops-api-overview`; Prometheus: `http://127.0.0.1:19090`; Jaeger: `http://127.0.0.1:16686`, service `fraudops-api`. A backend pod replacement can end its port-forward; restart that command if needed.

Stop manual forwards before cleanup. `bash scripts/exp004.sh cleanup` restores one EXP-003 base pod and removes the stack/demo resources. `bash scripts/exp003.sh cleanup` removes the entire named learning cluster. Images and evidence are retained. The cluster and stack were left running for inspection; cleanup was not executed in the accepted trial.

## Dashboard and PromQL

**FraudOps API Overview** has 12 provisioned panels. All queries and units are in [fraudops-overview.json](../../k8s/observability/grafana/fraudops-overview.json), with sources/providers using [Grafana file provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/). The main panels filter `job="fraudops-api",app="fraudops-api"`, excluding the alert demo; HTTP request/latency panels additionally filter `uri="/api/v1/predict"`.

| Panel | Query pattern / unit |
| --- | --- |
| Requests/s | `sum(rate(http_server_requests_seconds_count{...}[1m]))`, requests/s |
| HTTP error ratio | 4xx + 5xx completed requests / all completed predictions, fraction displayed 0–100% |
| HTTP p50/p95/p99 | `histogram_quantile(q, sum by (le) (rate(http_server_requests_seconds_bucket{...}[1m])))`, seconds, auto-displayed in ms |
| ONNX p50/p95/p99 | Same bucket aggregation for `fraud_onnx_inference_duration_seconds_bucket`, seconds |
| Decisions/s | `sum by (decision) (rate(fraud_decisions_total{...}[1m]))`, outcomes/s |
| Heap | `sum by (pod) (jvm_memory_used_bytes{...,area="heap"})`, bytes |
| GC count and pause time | Per-pod rates of `jvm_gc_pause_seconds_count` / `_sum`, events/s and pause-seconds/s |
| Healthy pod count and per-pod target health | `sum(up{...})` and `up{...}`, counts / scrape-health value |
| Inference outcomes | Per-outcome rates of `fraud_inference_total`, attempts/s |
| Process CPU and JVM threads | Process fraction and live thread count, separate axes |

Percentiles aggregate bucket rates **before** quantile calculation, following [Prometheus histogram semantics](https://prometheus.io/docs/prometheus/latest/querying/functions/#histogram_quantile). No per-pod percentiles are averaged. Histogram quantiles interpolate within buckets and are estimates, especially with few observations. Zero-rate histograms yield missing/NaN quantiles, not zero latency. For error ratio, a missing numerator becomes zero only when total-request data exists; no requests still produce an undefined ratio. Graphs do not connect missing intervals, and missing values display `No data`.

Availability panels measure successful scrapes of ready-discovered endpoints, not desired replicas or continuous readiness. Removing a target removes its series rather than necessarily creating an `up=0` sample. This distinction is visible during replacement.

The verifier retrieved the dashboard and executed every actual panel expression through Grafana's Prometheus data-source proxy, including finite HTTP and ONNX percentile values. It repeated these checks after deletion and rollout. [Normal panel results](evidence/runtime-20261023/normal-grafana-panel-data.json), [recovery results](evidence/runtime-20261023/recovery-grafana-panel-data.json), [rollout results](evidence/runtime-20261023/rollout-grafana-panel-data.json).

A browser inspection also confirmed rendered HTTP/ONNX curves, decision series and JVM heap plots. That check caught an unhelpful automatic error-ratio axis for an all-zero series; the final dashboard fixes its bounds to 0–1 (displayed 0–100%) and uses Grafana's supported 5s refresh. Prometheus still scrapes every 2s. Only Grafana was rolled for this display correction, followed by a targeted UI/API check; the API and its measured workload were not rerun for that change.

The [final dashboard definition](evidence/runtime-20261023/post-qa-grafana-dashboard.json) and [final panel query results](evidence/runtime-20261023/post-qa-grafana-panel-data.json) retain that correction. Grafana's Jaeger data-source proxy also retrieved the accepted final trace: [query response](evidence/runtime-20261023/grafana-jaeger-query.json).

## Scenario 1 — Normal inference, counters and trace correlation

Five parity fixtures passed before the workload and again after rollout, with unchanged tolerances (`abs_tol=1e-5`, `rel_tol=1e-4`), policy decisions, and model version. The controlled workload then alternated 100 typical/missing-numerical fixture requests, producing 50 reviews and 50 approvals. Prometheus's raw cumulative counters, after allowing post-workload scrapes, changed exactly as follows:

| Counter | Observed increase |
| --- | ---: |
| Prediction HTTP requests | 100 |
| Successful inference attempts | 100 |
| Model duration observations | 100 |
| Failed inference attempts | 0 |
| Approve / review decisions | 50 / 50 |

The normal trace [`cb9dbb43bc2432d15b752a349fcb61fa`](evidence/runtime-20261023/normal-trace.json) contains one root `http post /api/v1/predict` server span and one `onnx.inference` child. The verifier checks the child's `CHILD_OF` reference against the HTTP span ID, equal trace IDs, temporal containment, and exactly one server span, detecting duplicate HTTP instrumentation. Raw feature/account/probability attribute names were absent.

A fresh post-alert prediction produced trace [`0aab2e24b7aba9d20d74f30bb392b788`](evidence/runtime-20261023/final-trace.json). Its root span ID `244d6e6318b22b90` matches the prediction log line in [log-trace-correlation.json](evidence/runtime-20261023/log-trace-correlation.json). It was retrieved through Jaeger's query API, not inferred from an exporter startup message.

## Scenario 2 — Increased load and resources

The unchanged `scripts/load_test.py` ran with 200 warmup requests, then **2,000 measured requests at concurrency 16**. Its measured interval was 4.476s and throughput 446.84 requests/s. Warmup and measured traffic had no HTTP/transport errors. The driver checks HTTP success; the separate fixture-aware verifier checks prediction bodies.

| Measurement scope | p50 ms | p95 ms | p99 ms | Errors |
| --- | ---: | ---: | ---: | ---: |
| Client measured requests | 19.667 | 90.387 | 102.058 | 0 / 2,000 |
| Server HTTP histogram, 12s query window | 1.330 | 4.275 | 10.301 | No normal prediction errors observed |
| ONNX wrapper histogram, same window | 0.149 | 0.502 | 1.926 | No normal inference failures observed |

[Server query responses](evidence/runtime-20261023/load-server-histograms.json) use `histogram_quantile(q, sum by (le) (increase(bucket[12s])))` after a quiet interval before load and five seconds for final scrapes afterward. That window includes warmup and measured traffic, and differs from the client-only 2,000-request interval. `increase` extrapolates to range boundaries: its count estimates were approximately 2,445 HTTP and 2,448 ONNX observations, not literal request totals. The exact workload was 2,200 requests including warmup. Counter extrapolation and scrape phase also explain small differences between those two estimates.

Client latency includes network, port-forward/bridge processing and queueing; HTTP server time excludes much of that path, and ONNX time excludes HTTP validation/serialization. The bridge's eight workers can queue client concurrency 16. The measurements cannot be subtracted to isolate instrumentation overhead, nor compared directly to earlier native/Docker throughput as a causal regression. The earlier full scenario attempt measured 362.45 requests/s with the same client settings, illustrating trial variation; [its raw client result](evidence/attempt-1-load-client.json) is retained separately.

[Resource samples](evidence/runtime-20261023/load-resources.jsonl) combine available Prometheus JVM/process metrics with per-container cgroup v2 `memory.current`, `memory.peak`, and `cpu.stat`. Across the three API containers, sampled cgroup memory was **214.8–240.9 MiB**, and the maximum lifetime `memory.peak` was **241.9 MiB**. Heap samples were **28.2–48.5 MiB**. Each JVM's observed GC pause counter increased by two between samples; process CPU fractions ranged approximately 0.018–0.668. These gauges/snapshots are not synchronized, continuous utilization measurements; lifetime peak can include startup. The load driver's final heap reading (41,435,224 bytes) comes from one Service-selected JVM, not an aggregate of three pods.

All API containers retained 1 CPU / 512Mi limits and 250m / 256Mi requests. Sampled API restart counts remained zero; final API/stack pods were ready, with no observed OOMKilled state. `kubectl top pods` still returned [Metrics API not available](evidence/metrics-server.txt). No Metrics Server or cAdvisor scrape was installed. Kubernetes CPU utilization percentages, complete native-memory attribution, precise peak CPU, and stress headroom remain unmeasured.

## Scenario 3 — Pod deletion

During continuous fixture-checked requests, pod `fraudops-api-5c59d884db-cvxlh` was deleted with `--wait=false`. Relative to invoking that command:

| Observation | First observed time |
| --- | ---: |
| Replacement `...-7stdm` exists | 0.591s |
| Three non-terminating API pods Ready | 4.859s |
| Replacement is a healthy Prometheus target | 6.714s |
| Desired available count, old pod absent and ready-target set matches | 9.809s |

Ready count and scraped healthy count both dipped to two and returned to three. The old target could still be present briefly while readiness metadata propagated; target health and Kubernetes readiness are separate observations. The Grafana availability-panel query returned values from two to three, while its individual target-health series disappeared/appeared with pod identity changes. [Timeline](evidence/runtime-20261023/recovery-timeline.jsonl), [Grafana data](evidence/runtime-20261023/recovery-grafana-panel-data.json).

All **137 recovery requests** succeeded with valid predictions; a new post-deletion HTTP → ONNX trace was retrieved. Observer sample gaps were at most 0.644s; request-start gaps were at most 0.139s. No request downtime was observed. Availability between samples is inferred, not continuously proven. `minReadySeconds: 5` makes the Deployment-available milestone later than readiness.

## Scenario 4 — Real image update and telemetry continuity

The second image is built from `fraudops-api:exp004` using [revision/Dockerfile](../../k8s/observability/revision/Dockerfile). Its image configuration sets `FRAUD_IMAGE_REVISION=exp004-r1`, changing the active runtime log prefix, and an OCI version label. The deployed image reference and runtime image ID change; all three final pods emit `image=exp004-r1`. This is an application-image configuration revision with observable runtime behavior, not a pod-annotation restart. The jar/model/metadata hashes are identical between these two images: [base hashes](evidence/base-artifacts.sha256), [revision hashes](evidence/revision-artifacts.sha256). The jar's assembly version remains `1.0.0-exp001`; image revision and model version are separate.

The verifier executes `kubectl set image deployment/fraudops-api api=fraudops-api:exp004-r1`. Observed order, seconds after invoking the update:

| New pod suffix | Appears | Ready | Healthy scrape target | Old pod starts terminating |
| --- | ---: | ---: | ---: | --- |
| `qrc2r` | 0.619 | 4.915 | 7.991 | `7stdm` at 9.255 |
| `pfq2s` | 9.255 | 14.107 | 17.843 | `hmwpr` at 18.468 |
| `dlcjc` | 18.468 | 22.755 | 27.059 | `ptxfb` at 27.662 |

New pods initially reported not ready. Ready count never fell below three in the rollout samples. Healthy scrape count ranged three to four during rollout; terminating-pod overlap produced up to five total pods. Prometheus saw the new ready targets and removed the old ones. Convergence (three ready new-image pods, old pods gone, desired available count and target set restored) was observed by **32.652s**. [Timeline](evidence/runtime-20261023/rollout-timeline.jsonl), [rollout status](evidence/runtime-20261023/rollout-status.txt), [final targets](evidence/runtime-20261023/final-targets.json).

All **343 rollout requests** succeeded and matched the fixture; maximum request-start gap was 0.153s and maximum observer sample gap 0.643s. Grafana returned continuous finite HTTP/ONNX data, including changing target series, and Jaeger returned a new rollout HTTP → ONNX trace. Rates automatically account for counter resets through `rate`/`increase`; latency windows include overlapping pod lifetimes. No claim of an absence of sub-sample downtime is made.

Across the successful trial, **591 fixture-aware prediction requests** passed, including initial/final parity, normal/recovery/rollout and one post-alert request. Together with 2,200 load/warmup calls, 2,791 prediction HTTP calls completed without observed errors. Controlled demo failures below are separate and intentionally excluded from that count.

## Alerts — Defined, triggered, retrieved, and resolved

[alerts.yml](../../k8s/observability/config/alerts.yml) defines two per-`app` rules for prediction or explicit demo routes:

| Rule | Demonstration condition | Pending duration | Observed firing value |
| --- | --- | ---: | ---: |
| `FraudOpsElevated5xxRatio` | 5xx request rate / all requests over 30s > 20% | 10s | 48.889% |
| `FraudOpsHighP95Latency` | Aggregated HTTP histogram p95 over 30s > 100ms | 10s | 349.161ms |

The verifier explicitly creates [demo.yaml](../../k8s/observability/demo.yaml), which is not part of the default stack. This isolated Job's pods have `app=fraudops-alert-demo`, are excluded from the prediction Service, and alone enable the demo controller. It has a 180s active deadline, no retries, a TTL, and fixed 160-request / 180s endpoint budget. No arbitrary delays or failure rates can be configured through requests.

The demo failure route passes an empty map directly to the real instrumented model wrapper, yielding its existing `InferenceException` and HTTP 500 without modifying the model. The latency route sleeps a fixed 300ms and returns 200. Both consume the same finite budget; expired/exhausted requests return 429. Normal API pods never enable these routes. In the successful trial, **31 intentional 500s and 31 delayed 200s** were generated. The demo inference-error series increased, retained in [alert-demo-inference-errors.json](evidence/runtime-20261023/alert-demo-inference-errors.json).

Both rules became active at 19:16:59.653 UTC and were simultaneously observed `firing` at 19:17:11.078 UTC. The Prometheus API response retains label `app="fraudops-alert-demo"`, firing state and numeric value for each rule. [Firing response](evidence/runtime-20261023/alerts-firing.json), [pending/firing history](evidence/runtime-20261023/alerts-history.json). The verifier removed the Job/Service, waited for the rolling windows to clear, and verified [an empty alert list](evidence/runtime-20261023/alerts-resolved.json). It then retrieved a fresh successful prediction trace.

These short windows, small sample sizes and permissive volume assumptions are demonstrations, not business-approved SLOs or paging thresholds. Normal prediction traffic did not fire the rules. Firing was tested on the isolated app using the same rule expressions/instrumentation; normal API fault injection was deliberately not performed. Alertmanager, notification delivery and production alert tuning are not implemented.

## Failures and validation record

Failures were retained and resolved:

1. Initial direct `kind load docker-image` of the multi-platform component images failed with a missing content digest. A Docker host-platform-only archive loaded successfully. The reproducible helper uses that path; application images are still local and registry-free.
2. The first new integration test expected `/actuator/prometheus` while Boot's test configuration disabled metric exporting. Adding `@AutoConfigureObservability` to that test enabled the actual registry/endpoint. The final Docker build passed all 16 tests, including the original 15. [Test results](evidence/java-tests.txt).
3. An initial nested overlay attempted to include its ancestor directory and Kustomize reported a cycle. Moving shared resources to `k8s/base/` resolved it while preserving the EXP-003 root entry point.
4. The first full verifier attempt passed workloads, replacement, rollout and both alerts, but its final unfiltered “latest 100 traces” query was crowded by health/scrape traces. It correctly recorded incomplete status, not success. The query now selects the prediction operation and tests a fresh post-alert prediction. [Attempt 1 summary](evidence/attempt-1-summary.json), [its firing response](evidence/attempt-1-alerts-firing.json). Its full raw directory is retained locally under ignored `artifacts/EXP-004/attempt-1/`.
5. An immediate rerun initially rejected old terminating pods after `rollout status` had succeeded. The verifier now separately waits for exactly three ready base-image pods before starting. [Attempt 2 summary](evidence/attempt-2-summary.json); no workloads were run in that attempt. The third attempt completed all checks and is the canonical runtime evidence linked throughout this report.
6. A post-run in-container BusyBox `wget` query for series labels exited 139. The same Prometheus API request succeeded through a temporary localhost port-forward with Python; the Prometheus server remained healthy. An image-inspection template also required omitting optional OCI labels for images that do not contain them. Neither changed application behavior.
7. Browser QA found the all-zero error-ratio panel's automatic percentage scale unhelpful. Explicit 0–1 bounds and 5s refresh were applied, then the rendered dashboard and data-source queries were rechecked. Replacing that Grafana pod ended its original port-forward as expected; a new forward restored access.

| Validation level | Actual result |
| --- | --- |
| Existing Java tests / ONNX parity | All 15 original tests pass; five native Java/Python-reference parity cases remain within tolerance |
| New instrumentation regression | Integration test proves a real wrapper error increments `fraud_inference_total{outcome="error"}` alongside HTTP 500 and a timer observation |
| Full image build | Docker Java 21 stage ran `mvn clean verify`; 16 tests, zero failures/errors/skips |
| Static composition | Base, API overlay and stack render through `kubectl kustomize`; Bash/Python helper syntax passes; `git diff --check` passes |
| Prometheus rules/config | `promtool check config` validates discovery config and both rules; [output](evidence/promtool.txt) |
| Collector config | Image's `validate --config` command exited 0; [result](evidence/collector-validation.json) |
| Kubernetes schema/admission | Server dry-run passed; [output](evidence/server-validation.txt) |
| Runtime stack/inference | All six acceptance conditions demonstrated in the successful complete trial |
| Post-run QA | Exact log/trace correlation, actual metric label inspection, rendered Grafana HTTP/ONNX panels, corrected scale, and final panel queries checked |

## Limits and retained state

The final cluster runs three ready `exp004-r1` API pods, the existing independent client, and the four observability components: [post-QA status](evidence/runtime-20261023/post-qa-pods.json). The demo Job/Service are gone; both alert states resolved. No default kubeconfig/context or prior experiment evidence was overwritten. Verbose port-forward transport logs remain local and ignored by Git; concise machine-readable metric, trace, request, target, event and status evidence is retained in this repository.

The final accepted trial is short, with one pod deletion and one forward rollout; it is not a soak or statistically controlled performance study. There was no node outage, network partition, collector/backend failure, forced OOM, abrupt kill, prolonged in-flight request, backward rollout, or standalone horizontal-scaling observability trial. EXP-003 supplied the prior scaling experiment; dynamic discovery was runtime-verified here during replacement and forward rollout. Old-pod terminal states were not captured exhaustively; the observed deleted pod exited 143, consistent with SIGTERM, which alone does not prove graceful in-flight completion.

Metric polling and container reads add observer overhead, the bridge can become a bottleneck, and status/API samples are sequential rather than atomic. Server rate windows include warmup and extrapolation; dashboard quantiles at low traffic can be noisy. `up` is scrape health and cannot replace Deployment desired/ready gauges. No kube-state-metrics, HPA, service mesh, persistent monitoring storage, public access controls, external notification path, or infrastructure telemetry stack was added. Sensitive-payload absence was checked for generated traces, custom instrumentation and actual metric labels, not established as a blanket guarantee for every hypothetical future application change.
