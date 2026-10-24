FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md alembic.ini ./
COPY alembic ./alembic
COPY src ./src
RUN python -m pip install --no-cache-dir --no-deps .

# Build tooling stays outside the runtime images. EOSC workloads target amd64.
FROM base AS helm-download
RUN python -c "import hashlib, pathlib, urllib.request; data = urllib.request.urlopen('https://get.helm.sh/helm-v4.3.0-linux-amd64.tar.gz', timeout=120).read(); assert hashlib.sha256(data).hexdigest() == '86584a54def73570558f66f5111cc53dfed56689637ae32c1201205d494f54fb', 'Helm checksum mismatch'; pathlib.Path('/tmp/helm.tar.gz').write_bytes(data)" \
    && tar -xzf /tmp/helm.tar.gz -C /tmp linux-amd64/helm

FROM base AS publisher
RUN python -m pip install --no-cache-dir '.[messaging]' \
    && python -m pip check
ENV HOME=/tmp
USER 10001:0
CMD ["python", "-m", "mic3_api.publisher"]

FROM base AS worker
RUN python -m pip install --no-cache-dir '.[messaging,execution]' \
    && python -m pip check
COPY --from=helm-download /tmp/linux-amd64/helm /usr/local/bin/helm
COPY deploy/helm/model-run /app/deploy/helm/model-run
ENV HOME=/tmp \
    HELM_CACHE_HOME=/tmp/helm/cache \
    HELM_CONFIG_HOME=/tmp/helm/config \
    HELM_DATA_HOME=/tmp/helm/data \
    EXECUTION_CHART=/app/deploy/helm/model-run
USER 10001:0
CMD ["python", "-m", "mic3_api.worker", "--execute"]

# Keep the API last: existing builds without --target must still produce the API.
FROM base AS api
ENV HOME=/tmp
USER 10001:0
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "mic3_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
