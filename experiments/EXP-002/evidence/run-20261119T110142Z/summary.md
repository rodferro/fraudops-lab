## Build and image

| Check | Result |
| --- | --- |
| Image build | passed in 72.3 s; image sha256:8f24cc390afa, 607.4 MiB |
| Maven tests in build stage | 35 run, 0 failures, 0 errors, 0 skipped |
| Runtime user / app files | UID 10001: True; read-only: True |
| Model / metadata SHA-256 match fixture | True / True |
| Runtime JRE | OpenJDK Runtime Environment Temurin-21.0.8+9 (build 21.0.8+9-LTS) |
| Vulnerability scan | not run |

## Resource scenarios

| Scenario | Ready | Healthy | Parity | Throughput median | p99 median | Errors | Peak mem (cgroup) | Max health probe | OOM | Restarts | Final health |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- |
| baseline (2 CPU / 1g) | 1.69 s | 5.49 s | 6/6 | 1994.99 req/s | 67.981 ms | 0 | 303.3 MiB | 85.0 ms | False | 0 | healthy |
| constrained (1 CPU / 512m) | 3.27 s | 5.46 s | 6/6 | 794.75 req/s | 94.023 ms | 0 | 244.3 MiB | 456.5 ms | False | 0 | healthy |
| stress (0.5 CPU / 256m) | 14.02 s | 16.72 s | 6/6 | 212.51 req/s | 197.318 ms | 0 | 238.1 MiB | 2600.7 ms | False | 0 | healthy |

## SIGTERM under load

| Check | Result |
| --- | --- |
| `docker stop --time 35` duration | 0.23 s (SIGKILL needed: False) |
| Exit code / OOM | 143 / False |
| Requests in flight at SIGTERM | 16 total; 16 returned 200 |
| In-flight outcomes | {"total": 16, "200": 16} |
| Requests started after SIGTERM | {"total": 317, "200": 109, "RemoteDisconnected": 16, "ConnectionResetError": 31, "URLError": 161} |
| Graceful shutdown logged (commencing / complete) | True / True |
