r"""Shared RAG module (Project 4+).

Chunks the Markdown notes of the repo's knowledge base (the curated
`04-sistema-rag/notas/` folder) with Gemini embeddings and stores them in
ChromaDB, then allows semantic retrieval over them.

It deliberately reuses Project 4's knowledge base and its ChromaDB index
(same collection `notas_agentes`), so Project 6 retrieves from the exact same
data as Project 4 — no duplicated indexing.

Usage:
    import rag

    collection = rag.get_collection(reindex=False)
    results = rag.retrieve(collection, "What is MCP?", top_k=4)
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

_REPO_ROOT = Path(__file__).resolve().parent.parent

API_KEY = os.getenv("GEMINI_API_KEY")
EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")

DOCS_DIR = _REPO_ROOT / "04-sistema-rag" / "notas"
CHROMA_DIR = _REPO_ROOT / "04-sistema-rag" / "chroma_db"
COLLECTION_NAME = "notas_agentes"

CHUNK_SIZE = 800
OVERLAP = 150
TOP_K = 5
EMBEDDING_BATCH = 20

_client = None


def _get_client():
    """Return the Gemini client, creating it lazily on first use."""
    global _client
    if _client is None:
        if not API_KEY or API_KEY == "your_gemini_api_key_here":
            raise RuntimeError("RAG uses Gemini embeddings: set GEMINI_API_KEY in your .env.")
        _client = genai.Client(api_key=API_KEY)
    return _client


def _retry_seconds(e: Exception) -> float:
    """Read how long the API asks us to wait before retrying (default 60s)."""
    match = re.search(r"retry in ([\d.]+)s", str(e))
    if match:
        return float(match.group(1)) + 2
    return 60.0


def embed(texts: list[str]) -> list[list[float]]:
    """Convert texts into vectors with gemini-embedding-001.

    The free tier limits requests per minute; on a 429 we respect the time the
    API itself suggests before retrying.
    """
    vectors = []
    for i in range(0, len(texts), EMBEDDING_BATCH):
        batch = texts[i : i + EMBEDDING_BATCH]
        response = None
        for attempt in range(1, 6):
            try:
                response = _get_client().models.embed_content(
                    model=EMBEDDING_MODEL, contents=batch
                )
                break
            except Exception as e:
                print(
                    f"  [Lote de embeddings {len(vectors) // EMBEDDING_BATCH + 1}: error "
                    f"(intento {attempt}/5): {str(e)[:90]}]"
                )
                if attempt == 5:
                    raise
                time.sleep(_retry_seconds(e))
        for embedding in response.embeddings:
            vectors.append(list(embedding.values))
        time.sleep(0.5)
    return vectors


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = OVERLAP) -> list[str]:
    """Split text into chunks of ~size characters with overlap between them."""
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
    """Return [(name, text)] with the Markdown notes of the knowledge base."""
    documents = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if text.strip():
            documents.append((path.name, text))
    return documents


def index(collection):
    """Chunk, embed and store the documents in the Chroma collection."""
    print("Indexando la base de conocimiento...")
    documents = read_documents()

    chunks = []
    ids = []
    metadatas = []
    for name, text in documents:
        for i, chunk in enumerate(chunk_text(text)):
            chunks.append(chunk)
            ids.append(f"{name}#{i}")
            metadatas.append({"source": name, "position": i})

    print(f"Documentos: {len(documents)} | Fragmentos: {len(chunks)}")
    print("Convirtiendo fragmentos en embeddings...")
    vectors = embed(chunks)

    print("Guardando en ChromaDB...")
    collection.add(
        ids=ids,
        documents=chunks,
        embeddings=vectors,
        metadatas=metadatas,
    )
    print(f"Índice listo: {collection.count()} fragmentos.\n")


def get_collection(reindex: bool = False):
    """Return the Chroma collection, building the index if missing.

    Reuses the same collection (and ChromaDB folder) as Project 4.
    """
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
    if collection.count() == 0:
        index(collection)
    return collection


def retrieve(collection, question: str, top_k: int = TOP_K) -> list[dict]:
    """Retrieve the chunks most similar to the question.

    Returns a list of {"source", "text", "distance"}, ordered from most to
    least similar (lowest cosine distance first).
    """
    question_vector = embed([question])[0]
    results = collection.query(
        query_embeddings=[question_vector],
        n_results=min(top_k, collection.count()) or 1,
    )
    out = []
    for text, meta, distance in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        out.append({"source": meta["source"], "text": text, "distance": distance})
    return out