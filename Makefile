IMAGE        ?= k10y/k10y-agent
TAG          ?= 2.1.0
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

.PHONY: build test load deploy restart up token logs undeploy load-k3d push manifests hub-init hub-up hub-down hub-logs hub-backup hub-restore site-build site-dev

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

# --- hub (docker compose: hub + PostgreSQL) -------------------------------------------
hub-init:         ## Create .env with random database password, secret key and admin password
	@test -f .env || ( \
	  echo "POSTGRES_PASSWORD=$$(openssl rand -hex 24)" > .env && \
	  echo "K10Y_SECRET_KEY=$$(openssl rand -base64 48 | tr -d '\n')" >> .env && \
	  echo "K10Y_ADMIN_USER=admin" >> .env && \
	  echo "K10Y_ADMIN_PASSWORD=$$(openssl rand -base64 12 | tr -d '/+=\n')" >> .env && \
	  chmod 600 .env && echo "created .env" )
	@grep K10Y_ADMIN .env

hub-up: hub-init  ## Start hub + PostgreSQL (uses the local build of $(IMAGE):$(TAG))
	K10Y_IMAGE=$(IMAGE):$(TAG) docker compose up -d
	@echo "Hub: https://localhost:8443  (self-signed certificate)"

hub-down:         ## Stop hub + PostgreSQL (data stays in $(K10Y_DATA_DIR), default ./data)
	docker compose down

hub-logs:         ## Follow hub logs
	docker compose logs -f hub

hub-backup:       ## Back up the hub database now into $(K10Y_DATA_DIR)/backups (default ./data/backups)
	@mkdir -p $${K10Y_DATA_DIR:-./data}/backups
	@f=$${K10Y_DATA_DIR:-./data}/backups/k10y-$$(date -u +%Y%m%d-%H%M%S)-manual.sql.gz; \
	  docker compose exec -T postgres pg_dump -U k10y --clean --if-exists --no-owner k10y | gzip > $$f.part && mv $$f.part $$f && chmod 600 $$f && echo "wrote $$f"

hub-restore:      ## Restore the hub database from FILE=data/backups/<file>.sql.gz (replaces current data)
	@test -n "$(FILE)" || (echo "usage: make hub-restore FILE=data/backups/<file>.sql.gz" && exit 1)
	@test -f "$(FILE)" || (echo "no such file: $(FILE)" && exit 1)
	docker compose stop hub
	gunzip -c "$(FILE)" | docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 -U k10y -d k10y
	docker compose start hub
	@echo "restored $(FILE)"

# --- website (VitePress, in website/) ---------------------------------------------------
SITE_RUN = docker run --rm -v $(CURDIR):/repo -w /repo/website node:22-alpine sh -c

site-build:       ## Build the website into website/.vitepress/dist
	$(SITE_RUN) "apk add -q git >/dev/null && git config --global --add safe.directory /repo && npm ci --silent && npx vitepress build"

site-dev:         ## Preview the built website at http://localhost:4173
	docker rm -f k10y-site >/dev/null 2>&1 || true
	docker run -d --name k10y-site -p 4173:4173 -v $(CURDIR):/repo -w /repo/website node:22-alpine sh -c "npx vitepress preview --host 0.0.0.0 --port 4173"
	@echo "Website: http://localhost:4173"
