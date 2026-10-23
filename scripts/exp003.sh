#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Isolated credentials and explicit context; never change the user's kube context.
CLUSTER=fraudops-exp003
STATE="$PWD/artifacts/EXP-003"
mkdir -p "$STATE"
export KUBECONFIG="$STATE/kubeconfig"
export KIND_EXPERIMENTAL_PROVIDER=docker
NODE_IMAGE=kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5
k() { kubectl --context "kind-$CLUSTER" -n fraudops-exp003 "$@"; }

case "${1:-help}" in
  create)
    kind create cluster --name "$CLUSTER" --image "$NODE_IMAGE" \
      --config k8s/kind.yaml --kubeconfig "$KUBECONFIG" --wait 120s
    ;;
  images)
    # No remote application registry. Build EXP-002 only if absent locally.
    if ! docker image inspect fraudops-api:exp002 >/dev/null 2>&1; then
      docker build -t fraudops-api:exp002 .
    fi
    docker build -t fraudops-api:exp003-r1 -f k8s/revision/Dockerfile k8s/revision
    docker build -t fraudops-exp003-client:exp003 k8s/client
    kind load docker-image --name "$CLUSTER" \
      fraudops-api:exp002 fraudops-api:exp003-r1 fraudops-exp003-client:exp003
    ;;
  deploy)
    k apply -k k8s/
    k rollout status deployment/fraudops-api --timeout=180s
    k apply -f k8s/client/pod.yaml
    k wait --for=condition=Ready pod/fraudops-client --timeout=120s
    ;;
  verify)
    python3 scripts/exp003_verify.py --kubeconfig "$KUBECONFIG" \
      --output "${2:-$STATE/run-$(date -u +%Y%m%dT%H%M%SZ)}"
    ;;
  status)
    k get deployments,pods,services,endpointslices -o wide
    ;;
  cleanup)
    kind delete cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG"
    # Evidence and local images are deliberately retained; kubeconfig is ignored.
    ;;
  *)
    echo "Usage: bash scripts/exp003.sh {create|images|deploy|verify [output-dir]|status|cleanup}"
    ;;
esac
