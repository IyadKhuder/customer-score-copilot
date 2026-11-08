FROM node:24-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 POLICY_VERSION=v1.0 \
    COPILOT_CASE_DB=/app/var/api/cases.db COPILOT_SCORES_DB=/app/data/scores.db
WORKDIR /app
COPY requirements.txt requirements-workflow.txt requirements-api.txt constraints.txt ./
RUN python -m pip install --no-cache-dir -r requirements-api.txt && python -m pip check
COPY --from=frontend /frontend/dist/ ./frontend/dist/
COPY src/ ./src/
COPY policy_memos/ ./policy_memos/
COPY data/manifest.json data/scenarios.json data/scores_extended.csv ./data/
RUN python -m src.data_loader \
    && groupadd --gid 10001 copilot \
    && useradd --uid 10001 --gid copilot --no-create-home copilot \
    && mkdir -p /app/var/api && chown -R copilot:copilot /app/var
USER copilot
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=20s --retries=6 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)"
CMD ["python", "-m", "uvicorn", "src.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
