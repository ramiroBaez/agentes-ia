r"""Project 11 - Agent in production: from console to an HTTP API (FastAPI + Docker).

Takes the most complete agent of the series (Project 6: the online store from
Project 5 plus RAG over the AI Agents notes, running on LangGraph with memory)
and exposes it as an **HTTP API** with FastAPI, so any external system can use
it: a web app, a chatbot, an n8n workflow (Phase 8), etc.

The agent is NOT rewritten: it is imported as is (``rag_agent``). Only the
entry point changes (console -> HTTP request/response) and a production layer
is added: Pydantic validation, health endpoint, per-request logging and a
Docker image. The multi-provider fallback chain and the anti-quota backoff
still come from ``provider``, and the RAG index is the shared one from
``04-sistema-rag`` via ``rag``.

Endpoints:
    GET  /         -> API info + configured providers
    GET  /health   -> readiness (providers + RAG index), without spending tokens
    POST /ask      -> {"question": str, "session": str?} -> {"answer", "session", "provider", "steps"}

Memory is short-term and lives in the process (LangGraph MemorySaver): the
same "session" = the agent remembers the conversation; if no session is sent,
a new one is created (the ID travels back in the response to continue it).

Local usage (from the repo root):
    venv\Scripts\python.exe -m uvicorn 11-agent-in-production.api:app --host 127.0.0.1 --port 8000
    # or direct (uvicorn binds 0.0.0.0, Docker-friendly):
    venv\Scripts\python.exe 11-agent-in-production\api.py

Docker usage:
    docker compose -f 11-agent-in-production\docker-compose.yml up --build
"""

import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

import provider
import rag
import rag_agent

if sys.platform.startswith("win"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("api-agent")

RECURSION_LIMIT = 10  # max reasoning steps per question (anti loop)


# ------------------------------------------------------------------------- shared state
class ServiceState:
    """Process-wide state: the agent (LangGraph) + the RAG index."""

    def __init__(self) -> None:
        self.graph = None
        self.rag_collection = None
        self.rag_ok = False
        self.rag_reason = ""

    def start(self) -> None:
        """Build the graph and load the RAG index. Runs when the app boots."""
        log.info("Starting the Project 6 agent...")
        self.graph = rag_agent.build_graph()
        try:
            collection = rag.get_collection(reindex=False)
            n = collection.count()
            if n > 0:
                rag_agent.ACTIVE_COLLECTION = collection
                self.rag_ok = True
                self.rag_collection = collection
                log.info("RAG index loaded (%d chunks).", n)
            else:
                self.rag_reason = "The index exists but it is empty."
                log.warning("Empty RAG index; the agent will run without retrieval.")
        except Exception as e:
            self.rag_reason = str(e)[:200]
            log.warning("RAG index could not be loaded: %s", self.rag_reason)
        log.info(
            "API ready. Providers: %s | RAG: %s",
            ", ".join(provider.PROVIDERS) or "(none)",
            "yes" if self.rag_ok else "no (%s)" % self.rag_reason,
        )

    def ask(self, question: str, session: str) -> dict:
        """Run one question through the agent and return its answer."""
        config = {
            "configurable": {"thread_id": session},
            "recursion_limit": RECURSION_LIMIT,
        }
        # Internal steps of THIS question: the new agent turns in the session
        # history (the state is cumulative thanks to the checkpointer).
        try:
            previous = len(self.graph.get_state(config).values.get("messages", []))
        except Exception:
            previous = 0
        started = time.time()
        result = self.graph.invoke(
            {"messages": [provider.user_msg(question)]},
            config=config,
        )
        elapsed = time.time() - started
        steps = max(0, (len(result["messages"]) - previous + 1) // 2)
        last_agent = next(
            (m for m in reversed(result["messages"]) if m["role"] == "agent"), None
        )
        answer = (last_agent or {}).get("text", "").strip()
        if not answer:
            answer = "The agent could not build an answer for that question."
        log.info(
            "session=%s | provider=%s | steps=%d | %.1fs | answer=%d chars",
            session, provider.LAST_PROVIDER, steps, elapsed, len(answer),
        )
        return {
            "answer": answer,
            "provider": provider.LAST_PROVIDER,
            "steps": steps,
        }


service = ServiceState()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """On boot: build the graph and load the RAG index."""
    service.start()
    yield


app = FastAPI(
    title="Agent in production (P6: store + RAG)",
    description=(
        "API of Project 11: the Project 6 agent (online store with RAG over the "
        "AI Agents notes, LangGraph, per-session memory) exposed as an HTTP "
        "service. Ideal for consuming from web apps, bots or n8n."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ------------------------------------------------------------------------- Pydantic models
class Question(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="The user's message toward the agent.",
    )
    session: str | None = Field(
        None,
        max_length=128,
        description=(
            "Conversation ID. Same ID = the agent remembers the history. "
            "Empty = a new conversation is created."
        ),
    )


class AgentAnswer(BaseModel):
    answer: str = Field(..., description="The agent's final answer.")
    session: str = Field(..., description="The conversation ID (to continue it).")
    provider: str = Field(..., description="Which provider answered this time.")
    steps: int = Field(..., description="Internal turns of THIS question (model + tools).")


class ApiInfo(BaseModel):
    status: str
    providers: str
    active_model: str
    rag_indexed: int
    endpoints: set[str]


# ------------------------------------------------------------------------- routes
@app.get("/", response_model=ApiInfo)
def root() -> ApiInfo:
    """Basic information about the API and its configuration."""
    if service.graph is None:
        raise HTTPException(status_code=503, detail="The agent is still starting up.")
    return ApiInfo(
        status="ok",
        providers=", ".join(provider.PROVIDERS) or "(none)",
        active_model=provider.ACTIVE_MODEL,
        rag_indexed=service.rag_collection.count() if service.rag_ok else 0,
        endpoints={"/", "/health", "/ask"},
    )


@app.get("/health")
def health() -> dict:
    """Readiness: whether the agent answers and RAG is available. No tokens."""
    if service.graph is None:
        raise HTTPException(status_code=503, detail="The agent is still starting up.")
    return {
        "status": "ok",
        "providers": ", ".join(provider.PROVIDERS) or "(none)",
        "active_model": provider.ACTIVE_MODEL,
        "rag_available": service.rag_ok,
        "rag_chunks": service.rag_collection.count() if service.rag_ok else 0,
        "rag_reason": service.rag_reason or None,
    }


@app.post("/ask", response_model=AgentAnswer)
def ask(body: Question) -> AgentAnswer:
    """Send a question to the agent and get its answer.

    The agent decides on its own whether to use the store (stock, accounts,
    coupons), the RAG over the notes, or answer directly. The same 'session'
    accumulates short-term memory (the agent remembers the conversation), with
    a step limit per question.
    """
    if service.graph is None:
        raise HTTPException(status_code=503, detail="The agent is still starting up.")

    session = body.session or uuid.uuid4().hex
    log.info("POST /ask | session=%s | question=%d chars", session, len(body.question))

    try:
        result = service.ask(body.question, session)
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Agent execution error (session=%s)", session)
        detail = str(e)[:300]
        raise HTTPException(
            status_code=500,
            detail=f"The agent failed: {detail}",
        ) from e

    return AgentAnswer(
        answer=result["answer"],
        session=session,
        provider=result["provider"],
        steps=result["steps"],
    )


# ------------------------------------------------------------ "script" mode (embedded uvicorn)
if __name__ == "__main__":
    import uvicorn

    print(
        "API of Project 11 is up. Try it at:\n"
        "    http://127.0.0.1:8000/       (info)\n"
        "    http://127.0.0.1:8000/docs    (Swagger UI)\n"
        "    POST http://127.0.0.1:8000/ask"
    )
    uvicorn.run(app, host="0.0.0.0", port=8000)