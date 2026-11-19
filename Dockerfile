# syntax=docker/dockerfile:1.7
FROM maven:3.9.11-eclipse-temurin-21-noble@sha256:6fdc855a6ed81d288ca7ca37ac6ff5e9308b612485c0801d70b25a858c83d237 AS build

WORKDIR /workspace

COPY pom.xml ./
RUN --mount=type=cache,target=/root/.m2 \
    mvn -B -ntp dependency:go-offline

COPY src ./src
COPY fraud_model.onnx model_metadata.json ./

RUN --mount=type=cache,target=/root/.m2 \
    mvn -B -ntp clean verify

FROM eclipse-temurin:21.0.8_9-jre-noble@sha256:20e7f7288e1c18eebe8f06a442c9f7183342d9b022d3b9a9677cae2b558ddddd AS runtime

ARG APP_VERSION=1.0.0-exp001
ARG MODEL_VERSION=1.0.0-exp000

LABEL org.opencontainers.image.title="FraudOps Fraud Scoring API" \
      org.opencontainers.image.version="${APP_VERSION}" \
      com.fraudops.model.version="${MODEL_VERSION}"

WORKDIR /app

COPY --from=build --chown=0:0 --chmod=0444 \
    /workspace/target/fraud-scoring-api-1.0.0-exp001.jar /app/app.jar
COPY --from=build --chown=0:0 --chmod=0444 \
    /workspace/fraud_model.onnx /app/model/fraud_model.onnx
COPY --from=build --chown=0:0 --chmod=0444 \
    /workspace/model_metadata.json /app/model/model_metadata.json
# Bash /dev/tcp probe: no curl/wget in the image and no second JVM per probe.
# /opt/healthcheck must stay a traversable directory; k8s/client adds its class there.
COPY --chown=0:0 --chmod=0555 docker/healthcheck.sh /opt/healthcheck/healthcheck.sh

RUN chmod 755 /app /app/model /opt /opt/healthcheck

ENV SERVER_PORT=8080 \
    FRAUD_MODEL_PATH=/app/model/fraud_model.onnx \
    FRAUD_MODEL_METADATA_PATH=/app/model/model_metadata.json \
    JAVA_TOOL_OPTIONS="-XX:+UseContainerSupport -XX:InitialRAMPercentage=20.0 -XX:MaxRAMPercentage=60.0"

USER 10001:10001
EXPOSE 8080
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=10s --timeout=3s --start-period=35s --retries=3 \
    CMD ["/bin/bash", "/opt/healthcheck/healthcheck.sh"]

ENTRYPOINT ["java", "-jar", "/app/app.jar"]