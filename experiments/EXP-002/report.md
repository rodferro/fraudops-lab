# EXP-002 Docker Containerization Report

## Status: Runtime Acceptance Not Verified

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