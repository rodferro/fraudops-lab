# EXP-002 Docker Containerization Report

## Status: Runtime Acceptance Not Verified

> **Update 2026-11-19:** runtime acceptance was later verified on a Docker Engine host; see the [addendum](#addendum-2026-11-19-runtime-verification-on-a-docker-engine-host). The original text below is unchanged.

The container implementation is present, but EXP-002 cannot be declared complete on this workstation because Docker Engine and Docker-compatible builders are unavailable. `docker` is not on PATH; checks also found no Podman, nerdctl, or Buildah executable/service. As directed, the image build and all Docker-runtime acceptance criteria remain unverified. The Dockerfile and context were implemented and statically reviewed, and the application test/build plus the health-check helper were validated outside a container.

## Implementation

- `Dockerfile` is a BuildKit multi-stage build. The builder is `maven:3.9.11-eclipse-temurin-21-noble` pinned to manifest digest `sha256:6fdc855a6ed81d288ca7ca37ac6ff5e9308b612485c0801d70b25a858c83d237`; the runtime is `eclipse-temurin:21.0.8_9-jre-noble` pinned to `sha256:20e7f7288e1c18eebe8f06a442c9f7183342d9b022d3b9a9677cae2b558ddddd`.
- The builder caches Maven dependencies with BuildKit, runs `mvn clean verify` without skipping tests, and packages the existing Spring Boot executable JAR. Runtime files are the JAR, unchanged root-level `fraud_model.onnx` and `model_metadata.json`, and compiled `docker/HealthCheck.java` class. The model and service versions are distinct OCI labels.
- Runtime runs as numeric UID/GID 10001. The application/model files are root-owned mode `0444`; no credentials are added. The JRE health check invokes Java directly and calls `/actuator/health/readiness`, so it does not assume a shell, curl, or wget.
- `.dockerignore` excludes `target`, generated `data`, VCS/IDE files, Python caches/environments, local env files, logs, and common secret files while retaining all files required to build and test.
- Spring graceful shutdown is configured with a 25-second phase. Exec-form Java entrypoint and `STOPSIGNAL SIGTERM` allow normal signal delivery. Docker stop guidance reserves 35 seconds.
- JVM defaults enable container support and set initial/max heap percentages to 20%/60%, leaving headroom for ONNX Runtime native allocations. Heap/native behavior at the requested cgroup limits has not been measured.

## Available Verification

| Check | Result |
| --- | --- |
| `mvn clean package` with existing automated tests | Passed, 15 tests, 0 failures/errors/skips |
| Docker health helper Java 21 compilation | Passed |
| Health helper against live native `/actuator/health/readiness` | Passed |
| Native prediction and Actuator probes | Passed in EXP-001 packaged-jar check |
| Docker image build and image contents | Not run; no Docker Engine/CLI or alternate builder |
| Image vulnerability scan | Not run; no image scanner installed and no image could be built |

The Docker Hub digests were resolved from the registry manifest API. This verifies that those immutable image manifests exist, not that the Dockerfile builds successfully on this host. The native readiness-helper test does not demonstrate Docker `HEALTHCHECK` state behavior.

## Native Performance Context

Three new native packaged-jar trials used the existing `scripts/load_test.py`: 200 warmup requests, then 2,000 measured requests at concurrency 16, with the Python client and Java service on the same host. Hardware was Windows 11 Enterprise, Intel Core Ultra 5 235U (12 cores / 14 logical processors), 31.5 GB RAM, Java 21.0.12.1.

| Native metric | Median | Trial range |
| --- | ---: | ---: |
| Throughput | 793.91 req/s | 654.85-882.32 req/s |
| p50 latency | 18.179 ms | 16.992-20.221 ms |
| p95 latency | 35.208 ms | 27.551-47.922 ms |
| p99 latency | 47.993 ms | 37.257-68.543 ms |
| Request error rate | 0% | 0% in all trials |
| JVM heap used after run | 31,195,472 bytes | 22,649,776-51,557,520 bytes |

The pre-existing EXP-001 single-trial reference was 767.06 req/s, 18.038 ms p50, 38.395 ms p95, 53.111 ms p99, 0% errors, and 29,391,536 bytes heap. The spread across even native trials shows host/load variance. No container-side sample exists, so no Docker performance delta, container CPU utilization, container memory use, or container/native comparison is claimed. These end-to-end HTTP measurements are not comparable to EXP-000's ONNX-only `session.run` timing.

## Unverified Runtime Requirements

Docker was unavailable, so none of these resource cases were run: baseline 2 CPUs / 1 GiB, constrained 1 CPU / 512 MiB, or stress 0.5 CPU / 256 MiB. Startup time in a container, restart count, OOM events, container health status, request/parity behavior inside the image, native memory use under cgroups, and shutdown during concurrent container traffic are unknown. Native EXP-001 tests cover the same API, 96 concurrent predictions, five Python-generated parity cases, invalid startup artifacts, and health endpoints, but those results do not satisfy the container-specific checks.

To complete runtime acceptance, run the build, all three resource scenarios, container parity checks against `src/test/resources/parity-cases.json`, repeated container load trials, and a SIGTERM test while requests are in flight on a Docker Engine host. Verify completed requests and application shutdown/resource logs, not only the exit code. Then record `docker stats`, `docker inspect` health/restart/OOM state, heap Actuator samples, image contents/user, and scanner results if a scanner is available.

## Readiness

The configuration is a reasonable candidate for a local Kubernetes image build: Java 21, JRE runtime stage, non-root user, read-only application files, named model artifacts, a readiness-based Docker health check, and container-aware heap headroom are specified. It is **not yet verified as a deployable image**; the critical image-build, resource-limit, container parity, and graceful-shutdown criteria still require a machine with Docker Engine. No Kubernetes manifests or EXP-003 work were added.

## Addendum (2026-11-19): Runtime Verification on a Docker Engine Host

The sections above are the original EXP-002 record, written on a Windows workstation without Docker Engine, and are left unchanged. Docker became available later. The image was built and run during EXP-003, the repository migration and EXP-005, but none of those runs closed the EXP-002 acceptance criteria. This addendum closes them against the current Dockerfile.

### Changes since the original report

- **Dockerfile directory permissions.** The current Dockerfile adds `RUN chmod 755 /app /app/model /opt /opt/healthcheck` after the `COPY --chmod=0444` steps. The original report does not describe this line. EXP-003 records that it was already present and shows the failure it prevents: UID 10001 cannot traverse a directory created with mode 0444 (`ClassNotFoundException` in the EXP-003 test-client image). The static review above did not catch this. It is the only defect found so far that needed a real image to surface.
- **Test count.** The build-stage `mvn clean verify` now runs 35 tests, up from 15 in the original report, because tests were added in later experiments.
- **Parity fixture.** `src/test/resources/parity-cases.json` now holds six cases (five originally).
- **EXP-002B.** EXP-003 refers to earlier "EXP-002B performance and incomplete stress/shutdown work." No EXP-002B report or evidence is in the repository. This run covers the criteria that work was meant to close.

### Method

`scripts/exp002_verify.py` (Python standard library and the Docker CLI) ran once, from commit `265ef91`.

1. **Build.** `docker build` with `--no-cache-filter build`, so the Maven test stage ran again.
2. **Image checks.** `docker inspect`, then inside the image: `id`, file ownership and modes, a write attempt on `/app/model`, model and metadata SHA-256 compared against the parity fixture, and `java -version`.
3. **Resource scenarios.** Each scenario used a fresh container with `--restart=no` and `--memory-swap` equal to `--memory`, so exceeding the limit would end in an OOM kill instead of swapping. Scenarios: baseline 2 CPU / 1 GiB, constrained 1 CPU / 512 MiB, stress 0.5 CPU / 256 MiB. Each scenario recorded:
   - time to readiness `UP` and to Docker `healthy`, measured from the return of `docker run`
   - all six parity cases through the container API, using the fixture tolerances, expected decision and model version
   - three `scripts/load_test.py` trials of 2,000 requests at concurrency 16, each after 200 warmup requests
   - `docker stats` every 2 s
   - cgroup v2 `memory.peak`
   - the durations of the last five health-check probes
   - the final OOM, restart, exit and health state
4. **SIGTERM under load.** In the baseline scenario, 16 client threads sent requests continuously, and `docker stop --time 35` ran after 5 s. Each request was classified against the time the `docker stop` command started.

Host: Apple Silicon MacBook Air, Docker Desktop 29.9.3 (Linux VM with 10 CPUs, 7.75 GiB, aarch64, cgroup v2). The image is therefore the arm64 variant of the pinned multi-arch base images. The load client and the container shared the host.

Evidence: [`evidence/run-20261119T110142Z/`](evidence/run-20261119T110142Z/). The full raw run, including `docker inspect` output, the build log and `docker stats` samples, is in ignored `artifacts/EXP-002/run-20261119T110142Z/`.

### Results

| Check | Result |
| --- | --- |
| Image build | Passed in 72.3 s; image `sha256:8f24cc390afa…`, 607.4 MiB |
| Maven tests in build stage | 35 run, 0 failures, 0 errors, 0 skipped |
| Runtime user / app files | UID 10001; `/app/model` not writable |
| Model / metadata SHA-256 | Both match the parity fixture |
| Runtime JRE | Temurin 21.0.8+9 |
| Vulnerability scan | Not run (no scanner installed) |

| Scenario | Ready | Healthy | Parity | Throughput, trials 1/2/3 (req/s) | p95, trials 1/2/3 (ms) | Errors | cgroup peak | Max health probe | OOM / restarts | Final health |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Baseline 2 CPU / 1 GiB | 1.69 s | 5.49 s | 6/6 | 1,102 / 1,995 / 2,111 | 75.5 / 57.5 / 51.6 | 0 | 303.3 MiB | 85 ms | No / 0 | healthy |
| Constrained 1 CPU / 512 MiB | 3.27 s | 5.46 s | 6/6 | 313 / 795 / 1,057 | 99.5 / 87.4 / 80.7 | 0 | 244.3 MiB | 457 ms | No / 0 | healthy |
| Stress 0.5 CPU / 256 MiB | 14.02 s | 16.72 s | 6/6 | 129 / 213 / 259 | 202.0 / 106.0 / 102.6 | 0 | 238.1 MiB | 2,601 ms | No / 0 | healthy |

| SIGTERM check | Result |
| --- | --- |
| `docker stop` duration | 0.23 s, so no SIGKILL was needed |
| Exit code / OOM | 143 (SIGTERM) / no |
| Spring log | "Commencing graceful shutdown" then "Graceful shutdown complete" 7 ms later |
| Requests in flight when `docker stop` started | 16; all 16 returned 200 |
| Requests started after `docker stop` started | 317 total: 109 returned 200, 47 were reset or disconnected, 161 were refused |

### Findings

- **All three scenarios ran to completion.** There were no OOM kills, no restarts, no request errors, and parity was 6/6 through the runtime JRE in every case. In the stress case, cgroup peak memory reached 238.1 MiB of the 256 MiB limit in this run. A second stress run (see the follow-up below) reached the limit itself, so 256 MiB has no reliable headroom.
- **Throughput was still rising at the third trial.** In every scenario, each trial was faster than the one before, by up to 3.4× between trials 1 and 3 under the 1 CPU limit. The 200-request warmup is not enough for the JIT to settle, especially under CPU limits, so these figures are not steady-state throughput. Use the third trial as the closest estimate, and treat the medians in `summary.md` as warmup-affected.
- **The p50/p95 gap suggests CPU-quota throttling.** At baseline, p50 was about 3–5 ms while p95 was 52–76 ms, and the container was hitting its 200% CPU ceiling. That pattern is consistent with CFS throttling, but `cpu.stat` throttling counters were not captured, so this is not confirmed.
- **The health check nearly timed out under stress.** At 0.5 CPU under load, health-check probes took 1.8–2.6 s against a 3 s timeout. After the load stopped they took about 0.1 s. No probe failed, but each probe starts a second JVM, which is close to failing on a CPU-starved container and adds load at the worst moment. Consider a longer timeout, or a probe that does not start a JVM.
- **Graceful shutdown worked.** Every request in flight when `docker stop` started completed with 200, Spring logged a clean shutdown, and the process exited with code 143 well inside the 35 s budget. This shows little, though, because each request takes milliseconds. The test does not exercise a request that runs longer than the shutdown phase.
- **The after-stop classification overstates post-signal successes.** Requests were classified against when the `docker stop` command started, not when the JVM received the signal. The 109 successful "after SIGTERM" requests most likely started during that gap. The 47 resets show that clients hitting a stopping container see dropped connections. Kubernetes avoids this with endpoint removal and the existing `preStop` hook in `k8s/base/deployment.yaml`. Plain Docker does not.
- **Not comparable to the native baseline.** The original native figures came from an x86 Windows laptop. This run used an arm64 Mac, warmup was not settled, and the native trials themselves varied by about 35%. No container-versus-native overhead is claimed.

### Follow-up (2026-11-19): health check replaced

Because health-check probes came close to the timeout at 0.5 CPU, the Java probe (`docker/HealthCheck.java`, which started a second JVM for every probe) was replaced with `docker/healthcheck.sh`. The script sends the same `GET /actuator/health/readiness` over bash's `/dev/tcp` and passes only on HTTP 200 with `"status":"UP"`. The image has no curl or wget, so the check now depends on bash being in the Ubuntu-based JRE image. Before the image was rebuilt, the script was tested against a mock server: it passed for a healthy response and failed for a 503, a non-`UP` body, a closed port, and a server that hangs. The hang fails after 2 s, inside the 3 s timeout. A new `.gitattributes` keeps `*.sh` at LF line endings so a Windows checkout cannot break the script.

The rebuilt image was rerun in the stress scenario only (same host). Evidence: [`evidence/run-20261119T112147Z/`](evidence/run-20261119T112147Z/).

| Stress, 0.5 CPU / 256 MiB | Java probe (first run) | Bash probe (rerun) |
| --- | ---: | ---: |
| Maven tests / parity | 35 passed / 6/6 | 35 passed / 6/6 |
| Ready / healthy | 14.02 s / 16.72 s | 12.64 s / 15.35 s |
| Health probe under load | 1,796–2,601 ms | 397–667 ms |
| Health probe after load | 89–103 ms | 30–32 ms |
| Throughput, trials 1/2/3 (req/s) | 129 / 213 / 259 | 136 / 227 / 257 |
| p95, trials 1/2/3 (ms) | 202.0 / 106.0 / 102.6 | 201.7 / 104.3 / 101.8 |
| Max `docker stats` memory | 199.1 MiB | 236.7 MiB |
| cgroup `memory.peak` | 238.1 MiB | 256.0 MiB (at limit) |
| OOM / restarts / errors | No / 0 / 0 | No / 0 / 0 |

- **The worst probe under load dropped from 87% to 22% of the 3 s timeout.** The remaining delay under load is most likely CFS quota throttling: the probe shares the container's 0.5 CPU quota with a saturated JVM. It is not the cost of the probe itself, which took 30 ms after the load stopped.
- **Load results were unchanged within run-to-run noise.** With one run per variant, the change in p99 median (197 → 109 ms in `summary.md`) is not attributed to the probe.
- **Memory reached the 256 MiB limit without an OOM kill.** `memory.peak` includes reclaimable page cache, and the kernel reclaimed it instead of killing the process. The probe change is not the cause: the old Java probe ran in the same cgroup, so removing it can only lower memory use. Java heap stayed at 25–35 MB in both runs, so the variation is outside the heap. 256 MiB is at the edge of what this service needs and should be treated as a failure boundary, not a usable limit.

### Updated status

**Runtime acceptance verified on one Docker Desktop (arm64) host.** Three criteria passed in all scenarios:

- image build with tests, and the image contents checks
- container parity
- no OOM, restart or health failure in any resource scenario

Graceful shutdown under light, short-request load passed. The health check was replaced and rerun in the stress scenario (follow-up above). Still open: a vulnerability scan, steady-state (post-JIT) throughput, CPU throttling counters and memory breakdown (`cpu.stat`, `memory.stat`), memory headroom at 256 MiB, a shutdown test with long-running requests, and an x86 host run.
