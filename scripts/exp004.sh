#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
CLUSTER=fraudops-exp003
STATE="$PWD/artifacts/EXP-004"
mkdir -p "$STATE"
export KUBECONFIG="$PWD/artifacts/EXP-003/kubeconfig"
export KIND_EXPERIMENTAL_PROVIDER=docker
k() { kubectl --context "kind-$CLUSTER" -n fraudops-exp003 "$@"; }
case "${1:-help}" in
  build)
    docker build --build-arg APP_VERSION=1.0.0-exp004 -t fraudops-api:exp004 .
    docker build -t fraudops-api:exp004-r1 k8s/observability/revision
    ;;
  images)
    docker pull prom/prometheus:v3.2.1
    docker pull grafana/grafana:11.6.0
    docker pull otel/opentelemetry-collector:0.123.0
    docker pull jaegertracing/all-in-one:1.67.0
    # Docker Desktop's containerd store can export a multi-platform index with
    # unavailable sibling blobs. Export only the Docker server's platform.
    PLATFORM="linux/$(docker version --format '{{.Server.Arch}}')"
    docker image save --platform "$PLATFORM" -o "$STATE/images.tar" \
      fraudops-api:exp004 fraudops-api:exp004-r1 \
      prom/prometheus:v3.2.1 grafana/grafana:11.6.0 \
      otel/opentelemetry-collector:0.123.0 jaegertracing/all-in-one:1.67.0
    kind load image-archive --name "$CLUSTER" "$STATE/images.tar"
    ;;
  deploy)
    k apply -k k8s/observability/
    for component in prometheus grafana otel-collector jaeger; do
      k rollout status "deployment/$component" --timeout=180s
    done
    k apply -k k8s/observability/api/
    k rollout status deployment/fraudops-api --timeout=180s
    # Existing EXP-003 client bridges local requests to the real ClusterIP.
    k apply -f k8s/client/pod.yaml
    k wait --for=condition=Ready pod/fraudops-client --timeout=120s
    ;;
  verify)
    python3 scripts/exp004_verify.py --kubeconfig "$KUBECONFIG" \
      --output "${2:-$STATE/run-$(date -u +%Y%m%dT%H%M%SZ)}"
    ;;
  status)
    k get deployments,pods,services -o wide
    ;;
  cleanup)
    k delete -f k8s/observability/demo.yaml --ignore-not-found
    # Restore the EXP-003 one-replica base deployment before removing tracing.
    k apply -k k8s/
    k rollout status deployment/fraudops-api --timeout=180s
    k delete -k k8s/observability/ --ignore-not-found
    ;;
  *)
    echo "Usage: bash scripts/exp004.sh {build|images|deploy|verify [output-dir]|status|cleanup}"
    echo "Create the isolated cluster first with bash scripts/exp003.sh create if needed."
    ;;
esac
