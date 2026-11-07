#!/usr/bin/env bash
# End-to-end: a kind cluster with the agent, a hub (SQLite) connected to it, then the browser tests.
#   IMAGE=k10y-ci:test e2e/ci.sh        (the image must exist locally; Linux, Docker, kind and kubectl needed)
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE=${IMAGE:-k10y-ci:test}
CLUSTER=${CLUSTER:-k10y-e2e}
ADMIN_PW="E2e-admin-$(openssl rand -hex 6)"
NEW_PW="E2e-admin-$(openssl rand -hex 6)"
HUB=https://127.0.0.1:8443

cat > /tmp/kind.yaml <<KIND
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraPortMappings: [{containerPort: 30800, hostPort: 30800}]
KIND
kind create cluster --name "$CLUSTER" --config /tmp/kind.yaml --wait 120s
kind load docker-image "$IMAGE" --name "$CLUSTER"
sed -e "s#docker.io/k10y/k10y-agent:[0-9.]*#$IMAGE#" -e "s#imagePullPolicy: Always#imagePullPolicy: IfNotPresent#" deploy/install/install.yaml | kubectl apply -f -
kubectl -n k10y create configmap k10y-settings --from-literal=cluster-name=kind
kubectl -n k10y rollout status deploy/k10y-agent --timeout=180s
kubectl create deployment web --image=nginx:1.27-alpine --replicas=1 && kubectl rollout status deploy/web --timeout=120s
TOKEN=""
for _ in $(seq 30); do
  TOKEN=$(kubectl -n k10y get secret k10y-agent -o jsonpath='{.data.api-token}' 2>/dev/null | base64 -d || true)
  [ -n "$TOKEN" ] && break; sleep 2
done

docker run -d --name k10y-e2e-hub --network host -e K10Y_MODE=hub -e K10Y_PORT=8443 -e K10Y_TLS=true \
  -e K10Y_TLS_DIR=/tmp/tls -e K10Y_DATA_DIR=/tmp -e K10Y_DATABASE_URL=sqlite:////tmp/hub.db \
  -e K10Y_SECRET_KEY="$(openssl rand -hex 32)" -e K10Y_ADMIN_USER=admin -e K10Y_ADMIN_PASSWORD="$ADMIN_PW" "$IMAGE"
until curl -sk -o /dev/null "$HUB/healthz"; do sleep 2; done

api() { curl -sSk -b /tmp/e2e-cj -c /tmp/e2e-cj -H 'x-k10y-csrf: 1' -H 'content-type: application/json' "$@"; }
api -o /dev/null -d "{\"username\":\"admin\",\"password\":\"$ADMIN_PW\"}" "$HUB/api/hub/auth/login"
api -o /dev/null -d "{\"current_password\":\"$ADMIN_PW\",\"new_password\":\"$NEW_PW\"}" "$HUB/api/hub/auth/password"
api -o /dev/null -d "{\"name\":\"kind\",\"url\":\"https://127.0.0.1:30800\",\"token\":\"$TOKEN\",\"verify_tls\":false}" "$HUB/api/hub/clusters"

docker build -q -t k10y-e2e e2e >/dev/null
docker run --rm --network host -v "$PWD/e2e:/e2e:ro" -e K10Y_E2E_URL="$HUB" -e K10Y_E2E_USER=admin -e K10Y_E2E_PASSWORD="$NEW_PW" k10y-e2e
