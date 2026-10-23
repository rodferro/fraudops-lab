# EXP-003 — Kubernetes Deployment and Scaling

## Outcome and scope

Runtime acceptance **passed** on 2026-10-23. A dedicated local kind cluster demonstrated deployment and reference predictions, scaling from one to three ready pods, automatic replacement following deletion, and a real application-image update from `fraudops-api:exp002` to `fraudops-api:exp003-r1`. All 619 recorded prediction requests succeeded and matched the reference probability tolerance, decision, and model version `1.0.0-exp000`.

The measured scenario ran at **14:27:21–14:28:26 America/Sao_Paulo** (17:27:21–17:28:26 UTC). Raw evidence uses UTC. This is a single learning trial under light traffic, not a capacity or production availability claim. The cluster remains running with three revision pods for inspection; cleanup is provided below.

Primary evidence: [runtime summary](evidence/runtime-20261023/summary.json), [derived timeline analysis](evidence/runtime-20261023/analysis.json), and [all request outcomes](evidence/runtime-20261023/requests.jsonl). The analysis derives first-observed transitions and counts from the retained raw timelines; it does not estimate unobserved transitions.

## Environment and reproducibility

Observed environment: macOS ARM64, Docker Desktop Engine 29.9.2 with 10 CPUs and 8,319,504,384 bytes allocated to its Linux VM, kind v0.33.0, kubectl v1.37.1, Kubernetes v1.37.0, containerd 2.3.4. All application/test images were Linux ARM64. [Node and client/server versions](evidence/runtime-20261023/versions.json) and [image identities](evidence/images.txt) are retained.

The host had no usable Java runtime or Maven. Existing tests ran successfully in the Dockerfile's pinned Java 21/Maven build stage. Python 3.9 ran the standard-library verification driver.

From the repository root:

```bash
bash scripts/exp003.sh create
bash scripts/exp003.sh images
bash scripts/exp003.sh deploy
bash scripts/exp003.sh verify
bash scripts/exp003.sh status
```

For the recorded run, the last verification command supplied the evidence directory explicitly:

```bash
bash scripts/exp003.sh verify experiments/EXP-003/evidence/runtime-20261023
```

Each verification output directory must be new. Default output is timestamped under ignored `artifacts/EXP-003/`. To repeat after a completed trial, run `deploy` to restore one replica of the base image, then `verify`. Do not overwrite image tags during a trial. To reproduce the source image if missing, the `images` command builds the existing root Dockerfile, including its tests.

The helper creates only cluster `fraudops-exp003` and uses `artifacts/EXP-003/kubeconfig` with explicit context `kind-fraudops-exp003`. The default user kubeconfig is not modified. The node is pinned to `kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5`, as listed in the [kind v0.33.0 release notes](https://github.com/kubernetes-sigs/kind/releases/tag/v0.33.0).

Equivalent key commands, with the isolated kubeconfig exported:

```bash
export KUBECONFIG="$PWD/artifacts/EXP-003/kubeconfig"
export KIND_EXPERIMENTAL_PROVIDER=docker
kind create cluster --name fraudops-exp003 --config k8s/kind.yaml \
  --image kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5 \
  --kubeconfig "$KUBECONFIG" --wait 120s

docker build -t fraudops-api:exp003-r1 -f k8s/revision/Dockerfile k8s/revision
docker build -t fraudops-exp003-client:exp003 k8s/client
kind load docker-image --name fraudops-exp003 \
  fraudops-api:exp002 fraudops-api:exp003-r1 fraudops-exp003-client:exp003

kubectl --context kind-fraudops-exp003 apply -k k8s/
kubectl --context kind-fraudops-exp003 -n fraudops-exp003 \
  rollout status deployment/fraudops-api --timeout=180s
kubectl --context kind-fraudops-exp003 apply -f k8s/client/pod.yaml
```

Run cluster creation only once. The image-loading step is required: host Docker images do not automatically exist in kind's containerd. All manifests specify explicit tags and `imagePullPolicy: IfNotPresent`, following the [kind local-image workflow](https://kind.sigs.k8s.io/docs/user/quick-start/#loading-an-image-into-your-cluster). No application registry or credentials are required. A fresh environment needs network access to download the node, compiler, and base images.

Cleanup after stopping any interactive port-forward with Ctrl-C:

```bash
bash scripts/exp003.sh cleanup
# Equivalent: kind delete cluster --name fraudops-exp003 --kubeconfig "$KUBECONFIG"
```

Cleanup removes the named test cluster. It retains local images and experiment evidence. Credentials remain ignored by Git; generated artifacts and evidence are excluded from the root Docker build context.

## Deployment choices

[k8s/kustomization.yaml](../../k8s/kustomization.yaml) composes standard Namespace, ConfigMap, Deployment, and ClusterIP Service resources. There are no persistent volumes, external application dependencies, Helm resources, autoscalers, meshes, or monitoring stacks. The test client is a separate optional Pod used only for measurement.

| Setting | Value and reason |
| --- | --- |
| Initial replicas | 1; explicit horizontal scaling changes this to 3 |
| HTTP | Container and Service port 8080, named `http` |
| CPU request / limit | 250m / 1 CPU; lower scheduling reservation with room for startup/inference bursts; limit matches the prior Docker baseline |
| Memory request / limit | 256Mi / 512Mi; lower scheduling reservation, baseline-sized maximum; these choices are hypotheses rather than measured utilization requirements |
| JVM | Inherits 20% initial / 60% maximum heap sizing; leaves nominal room for native ONNX allocations, threads, metaspace, and other memory |
| Startup | Liveness HTTP endpoint every 2s, up to 60 failures; protects slow startup from liveness restarts |
| Liveness | `/actuator/health/liveness`, 10s period, 2s timeout, 3 failures |
| Readiness | `/actuator/health/readiness`, 2s period, 2s timeout, 2 failures; includes existing model initialization health |
| RollingUpdate | `maxSurge: 1`, `maxUnavailable: 0`, `minReadySeconds: 5`; new pods must remain ready before replacement proceeds |
| Termination | 35s total: 5s preStop sleep for endpoint propagation, Spring's existing 25s graceful phase, 5s margin; the delay cannot guarantee that every client observes endpoint changes immediately |
| Identity | UID/GID 10001, non-root, no privilege escalation, all Linux capabilities dropped, RuntimeDefault seccomp, no service-account token mount |
| Filesystem | Read-only root and model files; bounded 128Mi disk-backed `emptyDir` at `/tmp` for ONNX native-library extraction, Tomcat temporary files, and access logs; no persistent storage |

The application is in the Burstable QoS class. CPU requests do not reserve a full CPU, and heap percentage is not a total process-memory bound. Three steady API pods request 750m CPU / 768Mi memory. Terminating pods can overlap with new pods, so transient total pod/resource usage can exceed four pods even with `maxSurge: 1`; this behavior is described in the [Deployment documentation](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/).

The ConfigMap contains only model paths, port, unchanged policy threshold, and experiment-only Tomcat access-log configuration. It is injected as environment variables; changing it requires replacing pods to pick up new values. Logs record request IDs, HTTP status, and request line without feature payloads.

## Measurement paths

For interactive local access:

```bash
kubectl --context kind-fraudops-exp003 -n fraudops-exp003 \
  port-forward service/fraudops-api 8080:8080
```

The verifier uses local port 18080 for this Service-selected-pod path and checks all five cases in `src/test/resources/parity-cases.json`. As the [kubectl port-forward reference](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_port-forward/) explains, a Service resource selects a pod. This bypasses ClusterIP selection, so those requests alone cannot demonstrate Service balancing or availability after that pod disappears.

For actual Service traffic, the driver also starts `kubectl port-forward pod/fraudops-client 18081:8081`. The Java test client forwards requests to `http://fraudops-api:8080` inside the namespace, using fresh upstream TCP connections (`Connection: close`). It performs no retries. This preserves the inference API and does not add identity fields or headers to application responses. Per-request IDs in API access logs establish which backend handled each scaling request. The client is independent of the Deployment being deleted/updated.

During recovery and rollout, one request is sent at a time followed by a 100ms pause. A separate observer samples Pods, Deployment status, and EndpointSlices every 500ms plus API-call duration. Each recorded request validates probability (`abs_tol=1e-5`, `rel_tol=1e-4`), model version, and policy decision. Raw timestamps and latency are retained. Traffic begins about two seconds before each mutation and continues about three seconds after convergence. Timings start immediately before invoking the mutation command and include command/API latency.

Observed intervals between status samples were at most 0.653s during recovery and 0.645s during rollout. The three API reads in each sample are sequential, not an atomic snapshot; small differences between Pod and Deployment counts are possible. First-observed timings bound what the observer saw, rather than locating the exact controller transition. The request-start gaps were at most 0.133s and 0.136s respectively. There is no evidence about an outage shorter than or between those samples.

## Functional verification and scaling

The Deployment reached one ready pod with zero restarts. Both the local Service-selected port-forward and the in-cluster Service path passed all five parity cases. The typical request returned:

```json
{"fraud_probability":0.23597344756126404,"model_version":"1.0.0-exp000","decision":"review"}
```

The high-risk case returned `0.9930033683776855` against fixture `0.9930034279823303`, well within the recorded tolerance. The missing-value, unknown-category, and extreme-valid cases also passed.

The verifier then ran:

```bash
kubectl --context kind-fraudops-exp003 -n fraudops-exp003 \
  scale deployment/fraudops-api --replicas=3
kubectl --context kind-fraudops-exp003 -n fraudops-exp003 \
  rollout status deployment/fraudops-api --timeout=180s
```

All three pods became ready. The [scaled snapshot](evidence/runtime-20261023/scaled-state.json) records three ready EndpointSlice targets matching the pod names. Post-scaling parity passed. A further 120 requests traversed the ClusterIP Service with zero failures:

| Ready pod | Successful uniquely identified requests |
| --- | ---: |
| `fraudops-api-6748bc646b-fbzxz` | 39 |
| `fraudops-api-6748bc646b-prdt6` | 32 |
| `fraudops-api-6748bc646b-vqc65` | 49 |

The three `scale-*-access.log` files in the evidence directory contain the request IDs and HTTP 200 responses. Counts sum to 120, proving that this trial's Service traffic reached all three ready backends. This does not imply round-robin selection, equal traffic distribution, or a throughput improvement.

## Failure recovery

While Service traffic continued, the verifier deleted original pod `fraudops-api-6748bc646b-vqc65` with `kubectl delete pod ... --wait=false`. This was the only initial API pod; the availability measurements use the independent client bridge rather than its direct port-forward.

| Observation relative to deletion invocation | Result |
| --- | ---: |
| Replacement pod first observed (`...-p9kwp`) | 0.522s |
| Three non-terminating ready pods restored | 4.829s |
| Three Deployment-available replicas, old pod absent | 9.210s |
| Minimum sampled ready count | 2 |
| Sampled prediction requests / errors | 129 / 0 |

The first readiness time comes from the [raw recovery timeline](evidence/runtime-20261023/recovery-timeline.jsonl) and [analysis](evidence/runtime-20261023/analysis.json). The original summary field `restored_observed_seconds` measures the stronger convergence condition including available replicas and removal of the deleted pod. `minReadySeconds: 5` explains the later available-replica milestone.

No request downtime was observed over the 14.361s request-start window; all sampled predictions succeeded. Continued application availability between requests is an inference, not a continuous measurement. Post-recovery parity also passed. The snapshot observed the deleted container exiting with code 143, reason `Error`, consistent with SIGTERM; no OOMKilled state was observed. This status alone does not prove in-flight graceful completion.

## Real application-image rolling update

[k8s/revision/Dockerfile](../../k8s/revision/Dockerfile) derives a new image from the local EXP-002 image and adds a read-only `revision.properties`. Spring imports that file through `SPRING_CONFIG_ADDITIONAL_LOCATION`; it changes the application name and active console-log pattern to include `image=exp003-r1`. The OCI application version is also `1.0.0-exp003-r1`. This is a changed image layer with observable runtime configuration, rather than a changed pod annotation or a restart of an unchanged image.

The jar's own version stays `1.0.0-exp001`, intentionally. SHA-256 checks of `/app/app.jar`, `/app/model/fraud_model.onnx`, and `/app/model/model_metadata.json` match exactly between the two images: [base hashes](evidence/base-artifacts.sha256), [revision hashes](evidence/revision-artifacts.sha256). The model hash is `60af5e22073af550a5bfb249d4590afba12d3529221deb3d8e83b46eaa71a757`. Java source and model behavior were not changed.

After both images were loaded into kind:

```bash
kubectl --context kind-fraudops-exp003 -n fraudops-exp003 \
  set image deployment/fraudops-api api=fraudops-api:exp003-r1
kubectl --context kind-fraudops-exp003 -n fraudops-exp003 \
  rollout status deployment/fraudops-api --timeout=180s
kubectl --context kind-fraudops-exp003 -n fraudops-exp003 \
  rollout history deployment/fraudops-api
```

The observer recorded this order relative to the image-update invocation:

| New revision pod suffix | First observed | First Ready | Old pod first observed terminating |
| --- | ---: | ---: | --- |
| `749jd` | 0.571s | 4.847s | `prdt6` at 9.884s |
| `5x7c7` | 9.884s | 14.178s | `p9kwp` at 19.125s |
| `dgrfg` | 19.125s | 23.395s | `fbzxz` at 27.758s |

New pods initially reported not ready. Startup probes recorded connection-refused warnings while Java was booting; pods then passed readiness without restarts. Old-pod termination followed readiness plus the configured availability delay. Some terminating pods overlapped with the next surge pod: the maximum sampled total was five, while the minimum ready count remained three. [Raw rollout timeline](evidence/runtime-20261023/rollout-timeline.jsonl).

At 33.369s, the observer saw exactly three ready revision pods, three available updated replicas, and no old pods. [Rollout status](evidence/runtime-20261023/rollout-status.txt) reported success; [history](evidence/runtime-20261023/rollout-history.txt) retained two revisions. The 345 sampled requests over a 38.481s request-start window had zero errors. Final fixture parity passed.

Every final pod's application logs include `image=exp003-r1`, and the recorded runtime image IDs differ between the base and revision. The base image ID was represented by containerd using an imported manifest reference; the final image ID used a config digest. These formats differ, so they should not be compared as equivalent digest types. Host image identities, the baked configuration file, and all three active log markers provide independent evidence of the image change.

## Resources, restarts, and shutdown observations

Every API container had requests 250m CPU / 256Mi and limits 1 CPU / 512Mi. Across the seven distinct API pods captured in the snapshots/timelines, maximum observed restart count was zero. All three final revision pods were `Running` and `Ready`; the final client pod was also ready with zero restarts. [Full final Pod specifications and status](evidence/runtime-20261023/final-pods.json).

No OOMKilled reason appeared in sampled API container current/last termination states or retained events. Sampled terminated old containers reported exit 143 / `Error`, consistent with SIGTERM. One old rollout pod disappeared between samples without a captured terminal state. No forced-kill or failed-preStop event was recorded. Old-pod shutdown logs were not streamed before removal, and the light traffic did not exercise long-running in-flight requests; graceful-completion guarantees remain unverified.

`kubectl top pods` returned `error: Metrics API not available`, captured in [metrics-server.txt](evidence/metrics-server.txt). Metrics Server was not installed. CPU/memory utilization, memory headroom, throttling, native-memory peaks, and OOM stress behavior were **not measured**. Limits and lack of observed restarts/OOM events do not establish capacity or prove that higher load fits.

## Failures, validation, and remaining limitations

The following failures occurred and were resolved before the measured trial:

1. The initial node-image choice `kindest/node:v1.36.2` returned registry `not found`. No cluster was created by that attempt. The reproducible helper uses the digest-pinned v1.37.0 image from kind's release list.
2. The first test-client image copied a class into a newly created directory with mode 0444, causing UID 10001 to fail with `ClassNotFoundException`. Inspecting the directory showed it lacked execute/traverse permission. The corrected client copies into the existing traversable `/opt/healthcheck` directory, and the failed client pod was replaced before measurement. Its earlier readiness/BackOff events remain in the [event evidence](evidence/runtime-20261023/final-events.json). No API pod entered CrashLoopBackOff.
3. Initial sandbox access to Docker was denied. Execution continued with the required Docker/kind/Kubernetes sandbox permissions. This was an execution boundary, not an application failure.
4. A Python byte-compilation check attempted to use the host's redirected cache directory outside the writable sandbox. Validation was rerun using in-memory `compile()` successfully; this did not affect the runtime verifier.

Validation completed:

| Check | Actual result |
| --- | --- |
| Existing Java tests | 15 passed, 0 failures/errors/skips; [test summaries](evidence/java-tests.txt) |
| Fresh Docker build-stage verification | `docker buildx build --no-cache-filter build --target build --load -t fraudops-exp003-tests:exp003 .` ran `mvn clean verify` successfully |
| Client image | Java 21 compilation and live proxy requests passed |
| Manifest rendering | `kubectl kustomize k8s/` passed |
| Server schema/admission validation | `kubectl apply --dry-run=server -k k8s/` passed; [output](evidence/server-validation.txt) |
| Helper syntax | `bash -n scripts/exp003.sh` and Python in-memory compilation passed |
| Deployment/prediction | Runtime verified, both local port-forward and ClusterIP paths |
| Horizontal scaling | Runtime verified, three ready endpoints and per-pod successful request evidence |
| Pod replacement | Runtime verified, first appearance and readiness/availability milestones measured |
| Application-image update | Runtime verified, new image and active per-pod runtime markers |
| Resource utilization / stress | Pending; Metrics API absent and no stress scenario was run |
| Exhaustive graceful shutdown | Pending; observed light-traffic success and selected termination states only |

The original Dockerfile already contained a local directory-permission fix at the start of this task; it was preserved. No application Java source, ONNX artifact, or metadata was edited. Prior EXP-002/EXP-002B performance and incomplete stress/shutdown work were not rerun or promoted to Kubernetes results.

Other limits: one control-plane node on one workstation; no node-failure, network-partition, unhealthy-running-pod injection, high-concurrency capacity, or sustained-soak trial; pod deletion was graceful rather than an abrupt machine loss. Health endpoints and routing worked in the measured scenarios, but probe-triggered restart after a hung process was not tested. Only one trial was run, and the independent client/port-forward and local control plane are themselves dependencies of the measurement. No HPA or automatic load-driven scaling was implemented. Runtime acceptance for the requested orchestration scenarios is complete; these broader tests remain outside that claim.
