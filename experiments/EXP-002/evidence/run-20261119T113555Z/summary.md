## Build and image

| Check | Result |
| --- | --- |
| Image build | passed in 65.8 s; image sha256:4e942ef27494, 607.4 MiB |
| Maven tests in build stage | 42 run, 0 failures, 0 errors, 0 skipped |
| Runtime user / app files | UID 10001: True; read-only: True |
| Model / metadata SHA-256 match fixture | True / True |
| Runtime JRE | OpenJDK Runtime Environment Temurin-21.0.8+9 (build 21.0.8+9-LTS) |
| Vulnerability scan | not run |

## Resource scenarios

| Scenario | Ready | Healthy | Parity | Throughput median | p99 median | Errors | Peak mem (cgroup) | Max health probe | OOM | Restarts | Final health |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- |
| baseline (2 CPU / 1g) | 1.72 s | 5.52 s | 6/6 | 2120.6 req/s | 62.201 ms | 0 | 307.2 MiB | 38.7 ms | False | 0 | healthy |
| constrained (1 CPU / 512m) | 3.29 s | 5.45 s | 6/6 | 834.97 req/s | 92.637 ms | 0 | 236.0 MiB | 56.7 ms | False | 0 | healthy |
| stress (0.5 CPU / 256m) | 13.5 s | 15.67 s | 6/6 | 222.18 req/s | 106.68 ms | 0 | 218.4 MiB | 503.9 ms | False | 0 | healthy |

## SIGTERM under load

| Check | Result |
| --- | --- |
| `docker stop --time 35` duration | 0.23 s (SIGKILL needed: False) |
| Exit code / OOM | 143 / False |
| Requests in flight at SIGTERM | 16 total; 16 returned 200 |
| In-flight outcomes | {"total": 16, "200": 16} |
| Requests started after SIGTERM | {"total": 106, "200": 42, "RemoteDisconnected": 16, "ConnectionResetError": 47, "URLError": 1} |
| Graceful shutdown logged (commencing / complete) | True / True |
