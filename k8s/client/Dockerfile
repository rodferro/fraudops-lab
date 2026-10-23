FROM maven:3.9.11-eclipse-temurin-21-noble@sha256:6fdc855a6ed81d288ca7ca37ac6ff5e9308b612485c0801d70b25a858c83d237 AS compile
WORKDIR /client
COPY ServiceClient.java .
RUN javac --release 21 ServiceClient.java
FROM fraudops-api:exp002
COPY --from=compile --chown=0:0 --chmod=0444 /client/ServiceClient.class /opt/healthcheck/ServiceClient.class
ENTRYPOINT ["java", "-Xms16m", "-Xmx64m", "-cp", "/opt/healthcheck", "ServiceClient"]
