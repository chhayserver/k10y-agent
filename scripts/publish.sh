#!/usr/bin/env bash
# Publish a release from this machine, after `make release VERSION=X.Y.Z` and a review of the changes:
# checks everything says X.Y.Z, builds and tests the image, commits and tags, pushes the multi-arch image to
# Docker Hub, packages the Helm charts, creates the GitHub release with all files, deploys the website and
# updates the local hub. Needs: docker (logged in to Docker Hub as k10y), helm, gh, and ~/.cloudflare-k10y.env.
set -euo pipefail
cd "$(dirname "$0")/.."
V=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
REPO=chhayserver/k10y-agent
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

step "Checks for $V"
[ -z "$(git status --porcelain --untracked-files=no -- . ':!deploy' ':!web' ':!website' ':!src' ':!pyproject.toml' ':!Makefile' ':!docker-compose.yml')" ] || { echo "commit or stash unrelated changes first"; exit 1; }
git rev-parse "v$V" >/dev/null 2>&1 && { echo "tag v$V already exists"; exit 1; }
docker buildx version >/dev/null && helm version --short >/dev/null && gh auth status >/dev/null 2>&1
docker run --rm -v "$PWD:/w" -w /w python:3.12-slim sh -c "pip -q install --root-user-action=ignore pyyaml && python scripts/release.py check $V"

step "Build and test"
make build TAG="$V"
make test TAG="$V"

step "Commit and tag"
git add -A deploy web website src pyproject.toml Makefile docker-compose.yml
git commit -q -m "Release $V" || true
git tag -a "v$V" -m "k10y $V"

step "Push the image (amd64 + arm64)"
make push TAG="$V"

step "Push git"
git push -q origin main "v$V"

step "Helm charts"
rm -rf dist/charts && mkdir -p dist/charts
for chart in k10y-agent k10y-hub; do
  helm lint "deploy/helm/$chart" >/dev/null
  helm package "deploy/helm/$chart" -d dist/charts >/dev/null
  cp "dist/charts/$chart-$V.tgz" "dist/charts/$chart.tgz"   # stable name for .../releases/latest/download/
done

step "GitHub release"
docker run --rm -v "$PWD:/w" -w /w --entrypoint python "k10y/k10y-agent:$V" scripts/release.py notes "$V" > dist/notes.md
gh release create "v$V" -R "$REPO" --title "k10y $V" --latest --notes-file dist/notes.md \
  deploy/install/install.yaml deploy/install/install-readonly.yaml deploy/install/rbac-helm.yaml docker-compose.yml \
  dist/charts/k10y-agent.tgz dist/charts/k10y-hub.tgz "dist/charts/k10y-agent-$V.tgz" "dist/charts/k10y-hub-$V.tgz"

step "Website"
make site-deploy

step "Local hub"
docker compose up -d hub
echo "Released $V: https://github.com/$REPO/releases/tag/v$V"
