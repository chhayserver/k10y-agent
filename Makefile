IMAGE        ?= k10y/k10y-agent
TAG          ?= 1.2.1
VM           ?= rke2
CLUSTER_NAME ?= lab
KUBECONFIG   ?= $(HOME)/.kube/rke2.yaml
NODE_IP      ?= 192.168.64.2
# Control actions on/off, and namespaces they may never touch.
CONTROL      ?= true
PROTECTED    ?= kube-system,k10y
export KUBECONFIG

# RKE2's embedded containerd, inside the Lima VM.
CTR = limactl shell $(VM) sudo /var/lib/rancher/rke2/bin/ctr \
      --address /run/k3s/containerd/containerd.sock -n k8s.io

.PHONY: build test load deploy restart up token logs undeploy dashboard dashboard-stop load-k3d push manifests

build:            ## Build the agent image with Docker
	docker build -t $(IMAGE):$(TAG) .

test:             ## Run unit tests inside the image
	docker run --rm --entrypoint sh -e K10Y_AUDIT_PATH=/tmp/audit.jsonl -v $(CURDIR)/tests:/tests $(IMAGE):$(TAG) \
	  -c "pip install --quiet --user pytest httpx && python -m pytest -q /tests"

# Docker Hub / registry publishing. REGISTRY_IMAGE=docker.io/<user>/k10y-agent
REGISTRY_IMAGE ?= docker.io/$(IMAGE)
PLATFORMS      ?= linux/amd64,linux/arm64

push:             ## Build multi-arch (amd64+arm64) and push :$(TAG) and :latest to REGISTRY_IMAGE
	@test -n "$(REGISTRY_IMAGE)" || (echo "set REGISTRY_IMAGE, e.g. make push REGISTRY_IMAGE=docker.io/<user>/k10y-agent" && exit 1)
	docker buildx build --platform $(PLATFORMS) \
	  --label org.opencontainers.image.source=https://github.com/zorouzumaki1111-cmd/k10y-agent \
	  --label org.opencontainers.image.version=$(TAG) \
	  -t $(REGISTRY_IMAGE):$(TAG) -t $(REGISTRY_IMAGE):latest --push .

manifests:        ## Regenerate deploy/install/install.yaml and install-readonly.yaml for $(TAG)
	.venv/bin/python scripts/gen_install.py $(TAG)

load: build       ## Import the image into the RKE2 node's containerd
	docker save $(IMAGE):$(TAG) | $(CTR) images import -

K3D_CLUSTER ?= edge
load-k3d: build   ## Import the image into a k3d cluster (K3D_CLUSTER=edge)
	k3d image import $(IMAGE):$(TAG) -c $(K3D_CLUSTER)

deploy:           ## Apply manifests; creates the API token once
	kubectl apply -f deploy/k8s/rbac.yaml
	kubectl apply -f deploy/k8s/rbac-self.yaml
ifeq ($(CONTROL),true)
	kubectl apply -f deploy/k8s/rbac-control.yaml
else
	kubectl delete -f deploy/k8s/rbac-control.yaml --ignore-not-found
endif
	kubectl -n k10y create configmap k10y-agent --from-literal=cluster-name=$(CLUSTER_NAME) \
	  --from-literal=control-enabled=$(CONTROL) --from-literal=protected-namespaces=$(PROTECTED) \
	  --dry-run=client -o yaml | kubectl apply -f -
	kubectl -n k10y get secret k10y-agent >/dev/null 2>&1 || \
	  kubectl -n k10y create secret generic k10y-agent --from-literal=api-token=$$(openssl rand -hex 24)
	kubectl apply -f deploy/k8s/agent.yaml
	kubectl -n k10y rollout status deploy/k10y-agent --timeout=120s

restart:          ## Restart the agent to pick up a newly loaded image
	kubectl -n k10y rollout restart deploy/k10y-agent
	kubectl -n k10y rollout status deploy/k10y-agent --timeout=120s

up: load deploy restart  ## Build, load and (re)deploy in one go

token:            ## Print the agent API token
	@kubectl -n k10y get secret k10y-agent -o jsonpath='{.data.api-token}' | base64 -d; echo

logs:             ## Tail agent logs
	kubectl -n k10y logs deploy/k10y-agent -f

undeploy:         ## Remove the agent from the cluster
	kubectl delete -f deploy/k8s/agent.yaml --ignore-not-found
	kubectl delete -f deploy/k8s/rbac-control.yaml --ignore-not-found
	kubectl delete -f deploy/k8s/rbac-self.yaml --ignore-not-found
	kubectl delete -f deploy/k8s/rbac.yaml --ignore-not-found

DASHBOARD_PORT ?= 8080

dashboard: build  ## Run the standalone dashboard (UI only, no cluster access) in Docker
	docker rm -f k10y-dashboard >/dev/null 2>&1 || true
	docker run -d --name k10y-dashboard --restart unless-stopped -p $(DASHBOARD_PORT):8080 \
	  -e K10Y_MODE=dashboard --read-only --cap-drop ALL --security-opt no-new-privileges \
	  $(IMAGE):$(TAG)
	@echo "Dashboard: http://localhost:$(DASHBOARD_PORT)"

dashboard-stop:   ## Stop and remove the standalone dashboard container
	docker rm -f k10y-dashboard
