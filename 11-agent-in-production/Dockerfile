# Project 11 — Agent in production (FastAPI).
#
# Build context = REPOSITORY ROOT (needed for the RAG knowledge base and so the
# vendored modules can resolve the 04-sistema-rag paths):
#
#     docker compose -f 11-agent-in-production/docker-compose.yml up --build
#
# The ChromaDB index is NOT baked into the image: it is mounted as a read-only
# volume at runtime (see docker-compose.yml), so the image stays reproducible
# and the notes pick up the host index directly.

FROM python:3.12-slim

WORKDIR /app

# Dependencies first: better cache usage when the source changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The API + the vendored agent modules (provider, rag, rag_agent = Project 6
# reused as is), plus the knowledge base notes for RAG indexing.
COPY 11-agent-in-production/ 11-agent-in-production/
COPY 04-sistema-rag/notas/ 04-sistema-rag/notas/

WORKDIR /app/11-agent-in-production

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]