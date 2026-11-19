#!/bin/bash
# Docker HEALTHCHECK for the FraudOps API: GET /actuator/health/readiness on
# loopback using bash's /dev/tcp. The image has no curl or wget, and starting a
# second JVM per probe took up to 2.6 s of a 3 s timeout at 0.5 CPU (EXP-002
# addendum). Exit 0 only for HTTP 200 with a top-level "status":"UP".
set -u
port="${SERVER_PORT:-8080}"

{ exec 3<>"/dev/tcp/127.0.0.1/${port}"; } 2>/dev/null || exit 1
printf 'GET /actuator/health/readiness HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n' >&3

IFS= read -r -t 2 status_line <&3 || exit 1
case "$status_line" in
  "HTTP/1."?" 200"*) ;;
  *) exit 1 ;;
esac

body=""
while IFS= read -r -t 2 line <&3 || [ -n "${line:-}" ]; do
  body+="$line"
  line=""
done
exec 3<&-

[[ $body =~ \"status\"[[:space:]]*:[[:space:]]*\"UP\" ]] || exit 1
exit 0
