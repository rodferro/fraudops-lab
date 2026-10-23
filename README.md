# FraudOps Fraud Scoring API

EXP-001 exposes the existing EXP-000 ONNX fraud model as a Java REST service. The model and preprocessing are not modified or duplicated in Java.

## Requirements and Versions

- Java 21
- Maven 3.9+
- Spring Boot 3.5.8
- ONNX Runtime Java 1.23.2
- JUnit 5 via the Spring Boot 3.5.8 dependency BOM

The ONNX model uses opset 17. Maven resolves the remaining Spring, Jackson, and test dependency versions through the Spring Boot BOM in `pom.xml`.

## Build and Run

Run commands from the repository root. The service loads `./fraud_model.onnx` and `./model_metadata.json` by default.

```powershell
mvn clean test
mvn package
java -jar target/fraud-scoring-api-1.0.0-exp001.jar
```

For development, `mvn spring-boot:run` starts the application on port 8080. A successful model load is required for application startup and readiness.

## Configuration

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `SERVER_PORT` | `8080` | HTTP port |
| `FRAUD_MODEL_PATH` | `./fraud_model.onnx` | ONNX model path |
| `FRAUD_MODEL_METADATA_PATH` | `./model_metadata.json` | Model metadata path |
| `FRAUD_REVIEW_THRESHOLD` | `0.06729906797409058` | Review when probability is greater than or equal to this value; must be finite and between 0 and 1 |

## Prediction API

`POST /api/v1/predict` accepts the nine EXP-000 named features. All fields are JSON numbers or `null`. A missing or `null` feature becomes IEEE `NaN` in its tensor, allowing the ONNX pipeline to apply its embedded imputation. Invalid types, infinities, negative nonnegative-domain values, fractional integer features, and invalid binary values return structured HTTP 400 errors. Unknown integer category codes are accepted and handled by the exported one-hot encoder as all-zero for that category.

```json
{
  "amount": 250.0,
  "account_age_days": 120,
  "transactions_last_24h": 5,
  "distance_from_home_km": 150.0,
  "device_age_days": 30,
  "country_mismatch": 1,
  "card_present": 0,
  "channel_code": 0,
  "merchant_category_code": 2
}
```

Example PowerShell request:

```powershell
$body = @{
  amount = 250.0
  account_age_days = 120
  transactions_last_24h = 5
  distance_from_home_km = 150.0
  device_age_days = 30
  country_mismatch = 1
  card_present = 0
  channel_code = 0
  merchant_category_code = 2
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/v1/predict `
  -ContentType 'application/json' -Body $body
```

The response contains `fraud_probability`, `model_version`, and a separate policy decision (`review` or `approve`). There is no automatic decline decision. Numeric features are amount in USD, ages in days, transaction count, and distance in kilometers. Binary features are `0` or `1`. `channel_code` values are 0 ecommerce, 1 in-store, and 2 mobile. `merchant_category_code` values are 0 grocery, 1 fuel, 2 electronics, 3 travel, and 4 digital goods. See [model_metadata.json](model_metadata.json) for the complete schema.

## Health

- `/actuator/health` reports application and model health.
- `/actuator/health/liveness` reports process liveness without running inference.
- `/actuator/health/readiness` includes model initialization status.
- `/actuator/metrics` exposes local Spring/Micrometer metrics, including JVM heap usage, for the experiment's load baseline. No Prometheus exporter is configured.

## Tests and Parity

```powershell
mvn test
python generate_parity_fixtures.py
mvn test
```

The parity fixture generator uses Python ONNX Runtime and the unchanged EXP-000 `fraud_model.onnx`; it does not retrain the model. Java tests compare five representative inputs against these references with absolute tolerance `1e-5` and relative tolerance `1e-4`. The test suite also covers mapping, missing values, threshold behavior, malformed input, artifact startup failures, health endpoints, and concurrent requests.

## Local Load Baseline

With the application running, use the repeatable standard-library load driver:

```powershell
python scripts/load_test.py --requests 2000 --concurrency 16 --warmup-requests 200
```

The output includes throughput, p50/p95/p99 end-to-end HTTP latency, error count/rate, and JVM heap used from Actuator. It performs 200 warmup requests by default. Results depend on host load and client/server placement; they are not directly comparable to EXP-000's in-process Python ONNX `session.run` latency.

## Docker (EXP-002)

Docker Engine with BuildKit enabled is required. Build from the repository root; `.dockerignore` excludes the generated dataset, Maven `target`, IDE files, Python caches/environments, and local secret files while retaining the model, metadata, Java source, tests, and health-check source.

```powershell
docker build -t fraudops-api:exp002 --build-arg APP_VERSION=1.0.0-exp001 --build-arg MODEL_VERSION=1.0.0-exp000 .
docker run --rm --name fraudops-api -p 8080:8080 --cpus=2 --memory=1g fraudops-api:exp002
```

The two image bases are pinned by version and immutable Docker Hub manifest digest: `maven:3.9.11-eclipse-temurin-21-noble` and `eclipse-temurin:21.0.8_9-jre-noble`. The build stage runs `mvn clean verify`, including tests and parity, with a BuildKit Maven cache. The runtime stage contains only the executable jar, the unchanged model and metadata, and a tiny Java readiness probe. It runs as UID/GID 10001; the application artifacts are root-owned and read-only. The app image version and model version are separate image labels.

The readiness probe is compiled during the build and executed by Java in the JRE image, so container health does not assume that a shell, curl, or wget exists. Check with `docker inspect --format '{{.State.Health.Status}}' fraudops-api` or query `http://localhost:8080/actuator/health/readiness`. Docker's health status is a container-level health signal; it does not itself restart an unhealthy container. Kubernetes liveness/readiness probes should target the separate Actuator liveness and readiness endpoints.

Configuration continues to use `SERVER_PORT`, `FRAUD_MODEL_PATH`, `FRAUD_MODEL_METADATA_PATH`, and `FRAUD_REVIEW_THRESHOLD`. `JAVA_TOOL_OPTIONS` defaults to container-aware sizing with `-XX:InitialRAMPercentage=20.0 -XX:MaxRAMPercentage=60.0`, leaving memory outside the Java heap for ONNX Runtime native allocations and the rest of the process. Override it when testing a specific limit, while reserving native headroom.

Initial resource-limit trials:

```powershell
docker run --rm --name fraudops-api --cpus=2 --memory=1g -p 8080:8080 fraudops-api:exp002
docker run --rm --name fraudops-api --cpus=1 --memory=512m -p 8080:8080 fraudops-api:exp002
docker run --rm --name fraudops-api --cpus=0.5 --memory=256m -p 8080:8080 fraudops-api:exp002
```

For each running case, run `python scripts/load_test.py --requests 2000 --concurrency 16 --warmup-requests 200`; capture `docker stats --no-stream`, `docker inspect` restart/OOM/health state, and `/actuator/metrics/jvm.memory.used?tag=area:heap`. The 256 MiB case is deliberately a stress trial; heap percentage limits are not a guarantee that native allocations will fit. Do not raise limits silently if startup or inference fails.

Spring uses graceful server shutdown with a 25-second shutdown phase. The Docker image is exec-form with Java as PID 1 and `STOPSIGNAL SIGTERM`; use `docker stop --time 35 fraudops-api` to allow the Spring phase plus a 10-second margin. To verify in-flight completion, generate concurrent traffic while stopping the container and check completed responses, logs, exit status, and resource cleanup. The Docker health state and shutdown behavior have not been runtime-verified on this workstation because Docker Engine is unavailable.

Image tagging convention: `fraudops-api:<experiment-or-release-tag>`, with app/model versions retained separately as OCI labels. No credentials are embedded in the image.

## Known Limitations

The EXP-000 classifier was trained on synthetic data and has modest held-out fraud recall. The API is an integration experiment, not a production fraud-control recommendation. Input ranges are basic semantic checks, not a business-approved schema; unknown category codes are intentionally accepted per the exported pipeline behavior. Local performance measurements are not deployment capacity guarantees. Docker build/runtime and resource-constrained behavior require validation on a host with Docker Engine. No Kubernetes, authentication, database, or Prometheus export is included.