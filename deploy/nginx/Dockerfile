# k10y/k10y-nginx: the official Nginx image plus the k10y agent in Nginx mode. Use it in place of the nginx
# image, with the same config mounts. The agent's API is on port 9443 (HTTPS, self-signed); its token is
# created on first start in /var/lib/k10y/token (keep /var/lib/k10y on a volume).
FROM python:3.12-slim AS wheel
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN rm -rf src/k10y_agent/web && pip wheel --no-cache-dir --no-deps -w /wheels .

FROM docker.io/library/nginx:1.28.0-bookworm
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends python3-venv >/dev/null \
 && rm -rf /var/lib/apt/lists/* && python3 -m venv /opt/k10y
COPY --from=wheel /wheels /wheels
RUN /opt/k10y/bin/pip install --no-cache-dir -q "$(ls /wheels/*.whl)[nginx]" && rm -rf /wheels
COPY LICENSE /usr/share/doc/k10y/LICENSE
COPY deploy/nginx/k10y-nginx-entrypoint.sh /k10y-nginx-entrypoint.sh
ENV K10Y_MODE=nginx K10Y_PORT=9443 K10Y_TLS=true K10Y_TLS_DIR=/var/lib/k10y/tls K10Y_TOKEN_FILE=/var/lib/k10y/token \
    PYTHONUNBUFFERED=1
LABEL org.opencontainers.image.licenses="AGPL-3.0-only" org.opencontainers.image.source="https://github.com/chhayserver/k10y-agent"
EXPOSE 80 443 9443
ENTRYPOINT ["/k10y-nginx-entrypoint.sh"]
