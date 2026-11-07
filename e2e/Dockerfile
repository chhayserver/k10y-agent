# Browser tests for a running k10y hub. Build once: make e2e-image
FROM mcr.microsoft.com/playwright/python:v1.55.0-noble
RUN pip install --break-system-packages --no-cache-dir pytest==8.3.3 playwright==1.55.0
WORKDIR /e2e
ENTRYPOINT ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "/e2e"]
