# syntax=docker/dockerfile:1
#
# Project 12 — Aurora Shop Ops (API + SPA behind nginx).
#
# Build context = REPOSITORY ROOT (the RAG notes live in 04-sistema-rag/notas,
# shared with Projects 4, 6 and 11). docker-compose.yml already points here:
#
#     docker compose -f 12-observability-and-security/docker-compose.yml up --build

# ---------------------------------------------------------------- the SPA
# Node is only needed to compile; the final image does not carry it.
FROM node:24-alpine AS spa
WORKDIR /app
COPY 12-observability-and-security/web/package.json 12-observability-and-security/web/package-lock.json ./
RUN npm ci
COPY 12-observability-and-security/web/ ./
RUN npm run build

# ---------------------------------------------------------------- the API
FROM python:3.14-slim AS api
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY 12-observability-and-security/requirements.txt 12-observability-and-security/requirements.txt
RUN pip install --no-cache-dir -r 12-observability-and-security/requirements.txt

# The code and the shared notes. rag.py finds `04-sistema-rag/notas` by walking
# up from its own folder, so no NOTES_DIR is needed inside the image.
COPY 12-observability-and-security/*.py 12-observability-and-security/
COPY 04-sistema-rag/notas/ 04-sistema-rag/notas/
WORKDIR /app/12-observability-and-security

# The database and the checkpoints live on a volume: the container is disposable.
# The checkpointer derives its file name from DB_PATH (agent-checkpoints.db next to it).
ENV DB_PATH=/data/agent.db \
    CHROMA_DIR=/data/chroma_db
VOLUME ["/data"]

# Unprivileged user: the image runs as root by default and does not need to.
RUN useradd --create-home --uid 10001 aurora \
    && mkdir -p /data \
    && chown -R aurora:aurora /app /data
USER aurora

EXPOSE 8000
# Readiness, not liveness: the check has to ask whether the app can WORK (that
# is, whether it has a database). With /health the container said "healthy"
# while the database did not exist and the first request returned 500.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)"

# One worker: SQLite and the graph live in the process. To scale you go to
# Postgres and an event bus, not to more workers.
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

# ---------------------------------------------------------------- nginx
FROM nginx:1.27-alpine AS web
COPY --from=spa /app/dist /usr/share/nginx/html
COPY 12-observability-and-security/deploy/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD wget -qO- http://127.0.0.1/healthz > /dev/null || exit 1
