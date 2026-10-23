# EXP-001 Java Fraud Scoring API Report

## Outcome

The Java 21 Spring Boot API loads the unchanged EXP-000 `fraud_model.onnx` at startup, validates the accompanying metadata and tensor schema, and reuses one ONNX Runtime session for concurrent requests. The packaged application, HTTP prediction path, readiness/liveness probes, input validation, startup failure behavior, and Python/Java probability parity all passed local verification. EXP-001 stops here; no EXP-002 work was started.

## Selected Versions

| Component | Version |
| --- | --- |
| Java | Eclipse Temurin 21.0.12.1 |
| Maven | 3.9.11 |
| Spring Boot | 3.5.8 |
| ONNX Runtime Java | 1.23.2 |
| JUnit Jupiter | 5.12.2 (Spring Boot dependency management) |
| Model ONNX opset | 17 |

## Implementation

The project uses the existing root-level EXP-000 model and metadata instead of copying or reorganizing them. `OnnxFraudModel` reads the metadata feature names, checks all nine input tensors are `float32 [batch,1]`, checks `probabilities` is `float32 [batch,2]`, and reads positive-class column 1. It creates one session at application startup, creates and closes per-request tensors/results, and closes the session and session options through lifecycle hooks. Inference uses named tensors, so JSON property order does not affect scoring.

`POST /api/v1/predict` accepts the exact EXP-000 fields: `amount`, `account_age_days`, `transactions_last_24h`, `distance_from_home_km`, `device_age_days`, `country_mismatch`, `card_present`, `channel_code`, and `merchant_category_code`. Missing or JSON `null` values become `NaN`; ONNX performs the original imputing and encoding. The Java API does not duplicate preprocessing. Numeric types, finiteness, float32 range, nonnegative domains, integer fields, and binary values are validated. Unknown integer category codes are passed through, matching the embedded `OneHotEncoder(handle_unknown="ignore")` behavior.

The response separates `fraud_probability` and `model_version` from the configurable decision. `FRAUD_REVIEW_THRESHOLD` defaults to the EXP-000 validation reference `0.06729906797409058`; values at or above it return `review`, lower scores return `approve`. There is no automatic decline. Invalid JSON, missing bodies, invalid feature values, and inference failures have structured responses; internal exception text is not returned to clients. Missing or invalid model artifacts fail application initialization, so readiness cannot become successful without a usable model.

Actuator exposes `/actuator/health`, `/actuator/health/liveness`, `/actuator/health/readiness`, and local `/actuator/metrics`. Liveness reports process state and does not run inference. Readiness includes the model health indicator. No Prometheus exporter, container, Kubernetes resources, database, authentication, or message queue was added.

## Verification

`mvn clean package` passed. The final suite contains 15 tests with 0 failures, 0 errors, and 0 skipped:

- Service unit tests cover named feature mapping, missing-to-NaN behavior, unknown category passthrough, invalid numeric/binary values, threshold equality, and model-version propagation.
- Integration tests cover actual ONNX startup and prediction, malformed/wrong-type requests, missing bodies, health probes, and 96 concurrent predictions.
- Startup tests verify both missing and corrupt model artifacts fail the Spring application context.
- Five parity cases cover typical and high-risk inputs, missing numerical values, unknown categories, and extreme but finite values.

Python references were generated with Python ONNX Runtime `1.31.0` from the existing EXP-000 ONNX artifact by `generate_parity_fixtures.py`; the model was not retrained or modified. Java ONNX Runtime `1.23.2` matched all five expected probabilities using the EXP-000 tolerances (absolute `1e-5`, relative `1e-4`). The fixture and JUnit test are in `src/test/resources/parity-cases.json` and `src/test/java/com/fraudops/inference/OnnxFraudModelParityTest.java`.

The packaged jar started successfully and served this example request with probability `0.23597344756126404`, model version `1.0.0-exp000`, and decision `review`. Readiness and liveness both returned `UP`.

## Performance Baseline

Measured against the packaged jar on one Windows 11 Enterprise host: Intel Core Ultra 5 235U, 12 cores / 14 logical processors, 31.5 GB physical memory, Java 21.0.12.1. Python standard-library client and Java service ran on the same machine over loopback. The benchmark performed 200 concurrent warmup requests, then 2,000 timed requests at concurrency 16.

| Measure | Result |
| --- | ---: |
| Throughput | 767.06 requests/second |
| p50 end-to-end latency | 18.038 ms |
| p95 end-to-end latency | 38.395 ms |
| p99 end-to-end latency | 53.111 ms |
| Errors | 0 / 2,000 (0%) |
| JVM heap used after run | 29,391,536 bytes (Actuator sample) |

The repeatable driver is `scripts/load_test.py`. Its timings include HTTP request/response processing and loopback overhead and use a fresh standard-library request per operation; JVM heap is sampled after the timed run. Results are a local baseline, not capacity guarantees. They are not directly comparable to EXP-000's 29.2 microsecond Python ONNX `session.run` timing, which excluded HTTP and service overhead. The benchmark is a small single-host experiment and does not characterize production networks, long-duration stability, or saturation behavior.

## Limitations and Readiness

The model is trained on synthetic data and has modest held-out recall; successful Java parity only establishes that the service faithfully serves the model, not that its risk score is production-effective. API semantic bounds are intentionally basic, and unknown category values follow the model's all-zero encoding. Deployment hardware, network latency, and real workload mix remain unmeasured.

The API and ONNX contract are ready for the next integration stage. Production use still requires approved real data, domain review of the feature contract and decision policy, security controls, and deployment-specific load and reliability testing.