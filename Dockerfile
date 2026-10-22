FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install ".[server]"

FROM python:3.12-slim
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin k10y
COPY --from=build /install /usr/local
USER 10001
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
ENTRYPOINT ["k10y-agent"]
CMD ["serve"]
