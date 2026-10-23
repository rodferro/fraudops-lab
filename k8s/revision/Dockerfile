ARG BASE_IMAGE=fraudops-api:exp002
FROM ${BASE_IMAGE}
# A new, baked-in Spring configuration changes runtime log output and app name.
# The inference jar and model are inherited byte-for-byte from EXP-002.
COPY --chown=0:0 --chmod=0444 revision.properties /app/revision.properties
ENV SPRING_CONFIG_ADDITIONAL_LOCATION=file:/app/revision.properties
LABEL org.opencontainers.image.version="1.0.0-exp003-r1"
