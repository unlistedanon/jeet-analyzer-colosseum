FROM python:3.12-slim AS offline-demo-build
WORKDIR /source
COPY scripts/demo_flagship.py ./scripts/demo_flagship.py
COPY demo/flagship-evidence.v1.json ./demo/flagship-evidence.v1.json
RUN python scripts/demo_flagship.py

FROM node:22-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
COPY --from=offline-demo-build /source/frontend/public/demo/flagship-result.json ./public/demo/flagship-result.json
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    JEET_ANALYZER_API_HOST=0.0.0.0 \
    JEET_ANALYZER_API_PORT=8000 \
    JEET_BETA_DATA_DIR=/data \
    JEET_BETA_FRONTEND_DIST=/app/frontend/dist

WORKDIR /app
RUN groupadd --system jeet && useradd --system --gid jeet --home-dir /app jeet && mkdir -p /data && chown jeet:jeet /data
COPY pyproject.toml README.md ./
COPY jeet_analyzer/ ./jeet_analyzer/
COPY jeet_analyzer_api/ ./jeet_analyzer_api/
COPY schemas/ ./schemas/
COPY --from=frontend-build /frontend/dist ./frontend/dist
RUN python -m pip install --no-cache-dir ".[ui]"

USER jeet
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]
CMD ["uvicorn", "jeet_analyzer_api.secure_app:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
