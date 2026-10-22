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

FROM python:3.12-slim
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin k10y \
 && mkdir -p /data && chown 10001:10001 /data
COPY --from=build /install /usr/local
USER 10001
ENV PYTHONUNBUFFERED=1
EXPOSE 8080 8443
ENTRYPOINT ["k10y-agent"]
CMD ["serve"]
