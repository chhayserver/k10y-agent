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
# The Kubernetes client ships an asyncio copy of itself (kubernetes/aio, ~64 MB, plus aiohttp and its
# dependencies) that k10y never imports. Leave it out to keep the image small.
RUN python - <<'PY'
import importlib.metadata as md, pathlib, shutil
site = next(pathlib.Path("/install/lib").glob("python3*/site-packages"))
shutil.rmtree(site / "kubernetes" / "aio", ignore_errors=True)
unused = {"aiohttp", "aiohttp-retry", "aiohttp_retry", "multidict", "yarl", "frozenlist", "aiosignal", "propcache", "aiohappyeyeballs", "attrs"}
for dist in list(md.distributions(path=[str(site)])):
    name, files = dist.metadata["Name"], list(dist.files or [])
    if name.lower() in unused:
        for f in files:
            (site / f).unlink(missing_ok=True)
for d in sorted(site.rglob("*"), key=lambda x: -len(x.parts)):
    if d.is_dir() and not any(d.iterdir()):
        d.rmdir()
PY
# Debug symbols in the compiled libraries (cryptography, psycopg, pydantic) aren't needed to run.
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends binutils >/dev/null \
 && find /install -name "*.so*" -type f -exec strip --strip-unneeded {} + \
 && rm -rf /var/lib/apt/lists/*

# 3. Helm, for rolling back Helm releases from the hub (opt-in on the agent). Checksums pinned from the Helm release.
FROM python:3.12-slim AS helm
ARG TARGETARCH
ARG HELM_VERSION=v3.22.1
ARG HELM_SHA256_amd64=896cf63684a42a723d61c980369d6e588330927deb13676ca0d85006dd6da0ea
ARG HELM_SHA256_arm64=4c29e286b279d170fa048ddfc3e4e83dcd069ca9c52102766ba672d7149e694d
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
