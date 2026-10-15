r"""Notes index for semantic retrieval (adapted from Project 4, 04-sistema-rag).

Indexes the Markdown notes (chunking + Gemini embeddings) into ChromaDB and
lets you query them. It is the same indexing as Project 4, copied here so the
project is self-contained.

Paths:
    - DOCS_DIR: the notes folder. `NOTES_DIR` wins if set; otherwise the repo's
      shared notes of Project 4 (`04-sistema-rag/notas`), found by walking up
      from this folder (see `_default_notes_dir`).
    - CHROMA_DIR: `./chroma_db/` (or `CHROMA_DIR`), the index; mounted as a
      volume in the container.

Usage:
    import rag

    collection = rag.get_collection(reindex=False)
    results = rag.retrieve(collection, "¿qué es MCP?", top_k=5)
    for r in results:
        print(r["source"], r["distance"], r["text"][:80])
"""

import os
import re
import time
from pathlib import Path

import chromadb
from dotenv import load_dotenv
from google import genai

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")

_HERE = Path(__file__).resolve().parent


def _default_notes_dir(here: Path | None = None) -> Path:
    """Where the course notes live when `NOTES_DIR` is not set.

    Inside the repo they are the shared notes of Project 4
    (`04-sistema-rag/notas`, a sibling of this project folder). Anywhere else
    (a loose copy, CI) there is nothing to index and the project folder is
    returned: `notes.py` degrades to "no results" instead of breaking the turn.
    The lookup never assumes a fixed depth: in the container the code lives at
    `/app` and an index like `parents[2]` raised IndexError at import time.
    """
    here = here or _HERE
    for parent in (here, *here.parents):
        candidate = parent / "04-sistema-rag" / "notas"
        if candidate.is_dir():
            return candidate
    return here


# `NOTES_DIR` wins over everything: in the container the notes are mounted
# read-only (compose) and that has to be stated explicitly.
DOCS_DIR = Path(os.getenv("NOTES_DIR") or _default_notes_dir())
# In the container the index must live on the data volume: if it stays in /app
# it is lost on every rebuild and the whole indexing is paid for again.
CHROMA_DIR = Path(os.getenv("CHROMA_DIR") or (_HERE / "chroma_db"))
COLLECTION_NAME = "notes_agents"


def has_notes_to_index() -> bool:
    """Whether the notes folder has any Markdown. Without it RAG has nothing to say."""
    return any(path.suffix.lower() == ".md" for path in DOCS_DIR.glob("*.md"))

CHUNK_SIZE = 800
OVERLAP = 150
TOP_K = 5
EMBEDDING_BATCH = 20

_gemini_client = None


def _client():
    """Return the Gemini client, creating it on first use (lazy)."""
    global _gemini_client
    if _gemini_client is None:
        if not API_KEY or API_KEY == "tu_api_key_acá":
            raise RuntimeError(
                "RAG usa embeddings de Gemini: definí GEMINI_API_KEY en el .env."
            )
        _gemini_client = genai.Client(api_key=API_KEY)
    return _gemini_client


def _retry_seconds(e: Exception) -> float:
    """Read from the API how long to wait before retrying (default 60s)."""
    match = re.search(r"retry in ([\d.]+)s", str(e))
    if match:
        return float(match.group(1)) + 2
    return 60.0


def embedder(texts: list[str]) -> list[list[float]]:
    """Turn texts into vectors with gemini-embedding-001.

    The free tier limits requests per minute; on a 429 it waits for the time
    the API itself suggests before retrying.
    """
    vectors = []
    for i in range(0, len(texts), EMBEDDING_BATCH):
        batch = texts[i : i + EMBEDDING_BATCH]
        response = None
        for attempt in range(1, 6):
            try:
                response = _client().models.embed_content(
                    model=EMBEDDING_MODEL, contents=batch
                )
                break
            except Exception as e:
                print(
                    f"  [Embeddings lote {len(vectors) // EMBEDDING_BATCH + 1}: error "
                    f"(intento {attempt}/5): {str(e)[:90]}]"
                )
                if attempt == 5:
                    raise
                time.sleep(_retry_seconds(e))
        for embedding in response.embeddings:
            vectors.append(list(embedding.values))
        time.sleep(0.5)
    return vectors


def chunk(text: str, size: int = CHUNK_SIZE, overlap: int = OVERLAP) -> list[str]:
    """Split the text into chunks of ~`size` characters with overlap between them."""
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= size:
        return [text]

    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        chunks.append(text[start:end].strip())
        if end >= n:
            break
        start = end - overlap  # overlap: resume before the cut
    return chunks


def read_documents() -> list[tuple[str, str]]:
    """Return [(name, text)] for the Markdown notes in DOCS_DIR."""
    documents = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if text.strip():
            documents.append((path.name, text))
    return documents


def _existing_ids(collection, ids: list[str]) -> set[str]:
    """Which of these ids are already in Chroma (Chroma does not accept huge lists)."""
    present: set[str] = set()
    for i in range(0, len(ids), 100):
        batch = ids[i : i + 100]
        present.update(collection.get(ids=batch).get("ids") or [])
    return present


def index(collection):
    """Index the notes that are missing, one document at a time.

    Documents are indexed one by one and chunks already stored are skipped:
    indexing means Gemini embeddings with a daily quota, and a run that dies
    halfway (a 429 at chunk 80 of 410) must not throw away what it already
    paid for. This way the next run picks up where it left off.
    """
    documents = read_documents()
    if not documents:
        print("No hay documentos .md para indexar.")
        return

    indexed = 0
    for name, text in documents:
        chunks = chunk(text)
        ids = [f"{name}#{i}" for i in range(len(chunks))]
        present = _existing_ids(collection, ids)
        missing = [(i, c) for i, c in enumerate(chunks) if f"{name}#{i}" not in present]
        if not missing:
            continue
        vectors = embedder([c for _, c in missing])
        collection.add(
            ids=[f"{name}#{i}" for i, _ in missing],
            documents=[c for _, c in missing],
            embeddings=vectors,
            metadatas=[{"source": name, "position": i} for i, _ in missing],
        )
        indexed += len(missing)
        print(f"  {name}: {len(missing)} chunks nuevos")

    print(f"Índice listo: {collection.count()} chunks ({indexed} nuevos).\n")


def _missing_chunks(collection) -> bool:
    """Whether the index is incomplete (documents that could never be stored)."""
    for name, text in read_documents():
        ids = [f"{name}#{i}" for i in range(len(chunk(text)))]
        if len(_existing_ids(collection, ids)) < len(ids):
            return True
    return False


def get_collection(reindex: bool = False):
    """Return the Chroma collection, indexing whatever is missing."""
    if not has_notes_to_index():
        raise FileNotFoundError(
            f"no hay archivos .md en {DOCS_DIR} (¿falta montar NOTES_DIR?)"
        )
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    persistent = chromadb.PersistentClient(path=str(CHROMA_DIR))
    if reindex:
        try:
            persistent.delete_collection(COLLECTION_NAME)
            print("Colección anterior eliminada.")
        except Exception:
            pass
    collection = persistent.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    # Not only when empty: if a previous run was cut short by quota, what is
    # missing gets completed now.
    if _missing_chunks(collection):
        index(collection)
    return collection


def retrieve(collection, question: str, top_k: int = TOP_K) -> list[dict]:
    """Find the chunks most similar to the question.

    Returns a list of dicts {"source", "text", "distance"}, ordered from most
    to least similar (smallest cosine distance first).
    """
    vector_question = embedder([question])[0]
    results = collection.query(
        query_embeddings=[vector_question],
        n_results=min(top_k, collection.count()) or 1,
    )
    output = []
    for text, meta, distance in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        output.append(
            {"source": meta["source"], "text": text, "distance": distance}
        )
    return output
