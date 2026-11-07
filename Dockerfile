# 1. Web UI (Vue 3 + TypeScript). Built on the build machine's platform; output is plain static files.
FROM --platform=$BUILDPLATFORM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# 2. Python package (agent + hub), with the built UI inside it.
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
COPY --from=web /web/dist ./src/k10y_agent/web
RUN pip install --no-cache-dir --prefix=/install ".[server]"

# 3. Helm, for rolling back Helm releases from the hub (opt-in on the agent). Checksums pinned from the Helm release.
FROM python:3.12-slim AS helm
ARG TARGETARCH
ARG HELM_VERSION=v3.16.3
ARG HELM_SHA256_amd64=f5355c79190951eed23c5432a3b920e071f4c00a64f75e077de0dd4cb7b294ea
ARG HELM_SHA256_arm64=5bd34ed774df6914b323ff84a0a156ea6ff2ba1eaf0113962fa773f3f9def798
RUN python - <<'PY'
import hashlib, io, os, tarfile, urllib.request
arch = os.environ["TARGETARCH"]
data = urllib.request.urlopen(f"https://get.helm.sh/helm-{os.environ['HELM_VERSION']}-linux-{arch}.tar.gz", timeout=120).read()
assert hashlib.sha256(data).hexdigest() == os.environ[f"HELM_SHA256_{arch}"], "helm checksum mismatch"
with tarfile.open(fileobj=io.BytesIO(data)) as t:
    member = t.extractfile(f"linux-{arch}/helm")
    open("/helm", "wb").write(member.read())
os.chmod("/helm", 0o755)
PY

FROM python:3.12-slim
LABEL org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.source="https://github.com/chhayserver/k10y-agent"
COPY LICENSE /usr/share/doc/k10y/LICENSE
COPY --from=helm /helm /usr/local/bin/helm
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin k10y \
 && mkdir -p /data && chown 10001:10001 /data
COPY --from=build /install /usr/local
USER 10001
ENV PYTHONUNBUFFERED=1
EXPOSE 8080 8443
ENTRYPOINT ["k10y-agent"]
CMD ["serve"]
