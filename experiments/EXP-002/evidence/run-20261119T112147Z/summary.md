## Build and image

| Check | Result |
| --- | --- |
| Image build | passed in 67.7 s; image sha256:450d35179e6a, 607.4 MiB |
| Maven tests in build stage | 35 run, 0 failures, 0 errors, 0 skipped |
| Runtime user / app files | UID 10001: True; read-only: True |
| Model / metadata SHA-256 match fixture | True / True |
| Runtime JRE | OpenJDK Runtime Environment Temurin-21.0.8+9 (build 21.0.8+9-LTS) |
| Vulnerability scan | not run |

## Resource scenarios

| Scenario | Ready | Healthy | Parity | Throughput median | p99 median | Errors | Peak mem (cgroup) | Max health probe | OOM | Restarts | Final health |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- |
| stress (0.5 CPU / 256m) | 12.64 s | 15.35 s | 6/6 | 227.18 req/s | 108.965 ms | 0 | 256.0 MiB | 667.3 ms | False | 0 | healthy |
