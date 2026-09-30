# Stage 1: build the React console.
FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npx tsc -b && npx vite build --outDir /web/dist

# Stage 2: run the pipeline once to produce data, models and reports (deterministic seeds).
FROM python:3.12-slim AS pipeline
ENV PYTHONUNBUFFERED=1 RELIABILITYML_MLFLOW=0 RELIABILITYML_HOME=/app
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY corpus ./corpus
COPY eval ./eval
RUN pip install --no-cache-dir . && python -m reliabilityml.pipelines.build

# Stage 3: lean runtime. No MLflow or training dependencies; the API loads the production model artifact.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 RELIABILITYML_MLFLOW=0 RELIABILITYML_HOME=/app RELIABILITYML_ARTIFACTS=/app/artifacts
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY corpus ./corpus
COPY eval ./eval
RUN pip install --no-cache-dir . && useradd --create-home app
COPY --from=pipeline /app/artifacts ./artifacts
COPY --from=web /web/dist ./src/reliabilityml/static
RUN pip install --no-cache-dir --no-deps . && chown -R app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health')"
CMD ["sh", "-c", "uvicorn reliabilityml.api.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
