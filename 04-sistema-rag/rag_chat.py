"""Mini RAG system: chat over your own knowledge base.

Indexes Markdown documents from the local `notas/` folder (chunking + Gemini
embeddings) into ChromaDB and answers questions about them: the question is
converted into a vector, the most similar chunks are retrieved, and that
context is injected into the LLM so it answers citing the real information
from the documents, not from the model's memory.

Deliverable: a console chatbot you can ask things about your documents and it
responds citing the actual information contained in them.

Usage:
    python rag_chat.py             # indexes if needed and chats
    python rag_chat.py --reindex   # rebuilds the index from scratch
"""

import argparse
import os
import re
import time
from pathlib import Path

import chromadb
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

if not API_KEY or API_KEY == "your_gemini_api_key_here":
    print("Error: set GEMINI_API_KEY in your .env file to call the API.")
    raise SystemExit(1)

client = genai.Client(api_key=API_KEY)

EMBEDDING_MODEL = "gemini-embedding-001"
DOCS_DIR = Path(__file__).resolve().parent / "notas"
CHROMA_DIR = Path(__file__).resolve().parent / "chroma_db"
COLLECTION_NAME = "notas_agentes"

CHUNK_SIZE = 800
OVERLAP = 150
TOP_K = 5
EMBEDDING_BATCH = 20

ANSWER_SYSTEM_INSTRUCTION = (
    "You are an assistant specialized in the user's AI Agents knowledge base. "
    "Answer using ONLY the information from the context provided. If the answer "
    "is not in the context, say so clearly."
)


# ------------------------------------------------------------ API handling
def call_with_retries(func, *args, **kwargs):
    """Call func with retries; if the API asks to wait (429), respect that time."""
    delay = 1
    for attempt in range(1, 4):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            print(f"  [Error on call (attempt {attempt}/3): {str(e)[:110]}]")
            if attempt == 3:
                raise
            if "retry in" in str(e):
                pause = _retry_seconds(e)
            else:
                pause = delay
                delay *= 2
            print(f"  Retrying in {pause:.0f}s...")
            time.sleep(pause)


def _retry_seconds(e: Exception) -> float:
    """Read how long the API asks us to wait before retrying (default 60s)."""
    match = re.search(r"retry in ([\d.]+)s", str(e))
    if match:
        return float(match.group(1)) + 2
    return 60.0


def embed(texts: list[str]) -> list[list[float]]:
    """Convert a list of texts into vectors with gemini-embedding-001.

    The free tier limits to 100 requests per minute, so on a 429 response we
    respect the time the API itself suggests before retrying.
    """
    vectors = []
    for i in range(0, len(texts), EMBEDDING_BATCH):
        batch = texts[i : i + EMBEDDING_BATCH]
        response = None
        for attempt in range(1, 6):
            try:
                response = client.models.embed_content(
                    model=EMBEDDING_MODEL, contents=batch
                )
                break
            except Exception as e:
                print(
                    f"  [Batch {len(vectors) // EMBEDDING_BATCH + 1}: error "
                    f"(attempt {attempt}/5): {str(e)[:90]}]"
                )
                if attempt == 5:
                    raise
                time.sleep(_retry_seconds(e))
        for embedding in response.embeddings:
            vectors.append(list(embedding.values))
        time.sleep(0.5)
    return vectors


# ---------------------------------------------------------------- chunking
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


# ---------------------------------------------------------------- indexing
def read_documents() -> list[tuple[str, str]]:
    """Return [(name, text)] with the Markdown documents from the notas folder."""
    documents = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if text.strip():
            documents.append((path.name, text))
    return documents


def index(collection):
    """Chunk, embed and store the documents in the Chroma collection."""
    print("Indexing your knowledge base...")
    documents = read_documents()

    chunks = []
    ids = []
    metadatas = []
    for name, text in documents:
        for i, chunk in enumerate(chunk_text(text)):
            chunks.append(chunk)
            ids.append(f"{name}#{i}")
            metadatas.append({"source": name, "position": i})

    print(f"Documents: {len(documents)} | Chunks: {len(chunks)}")
    print("Converting chunks to embeddings...")
    vectors = embed(chunks)

    print("Saving to ChromaDB...")
    collection.add(
        ids=ids,
        documents=chunks,
        embeddings=vectors,
        metadatas=metadatas,
    )
    print(f"Index ready: {collection.count()} chunks.\n")


def get_collection(reindex: bool):
    persistent = chromadb.PersistentClient(path=str(CHROMA_DIR))
    if reindex:
        try:
            persistent.delete_collection(COLLECTION_NAME)
            print("Previous collection deleted.")
        except Exception:
            pass
    collection = persistent.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    if collection.count() == 0:
        index(collection)
    else:
        print(
            f"Using existing index ({collection.count()} chunks). "
            "Pass --reindex to rebuild it.\n"
        )
    return collection


# ------------------------------------------------------------------ query
def search(collection, question: str) -> dict:
    """Retrieve the chunks most similar to the question."""
    question_vector = embed([question])[0]
    results = collection.query(
        query_embeddings=[question_vector],
        n_results=TOP_K,
    )
    return results


def build_context(results) -> str:
    """Join the retrieved chunks with their source, ready for the prompt."""
    docs = results["documents"][0]
    metas = results["metadatas"][0]
    return "\n\n---\n\n".join(
        f"[{m['source']}]\n{d}" for d, m in zip(docs, metas)
    )


def main():
    parser = argparse.ArgumentParser(
        description="Mini RAG system over your AI Agents knowledge base."
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="Rebuild the index from scratch before chatting.",
    )
    args = parser.parse_args()

    collection = get_collection(args.reindex)

    answer_config = types.GenerateContentConfig(
        system_instruction=ANSWER_SYSTEM_INSTRUCTION,
    )

    print("RAG chat over your knowledge base.")
    print("Type a question (or 'exit' to quit).\n")

    while True:
        question = input("Question> ").strip()
        if not question:
            continue
        if question.lower() in ("exit", "quit", "q"):
            print("See you next time!")
            break

        print("  [Searching your documents...]")
        results = search(collection, question)
        context = build_context(results)
        metas = results["metadatas"][0]
        distances = results["distances"][0]

        prompt = (
            "Context (only data you may use):\n"
            f"{context}\n\n"
            f"User question: {question}"
        )

        try:
            response = call_with_retries(
                client.models.generate_content,
                model=MODEL,
                config=answer_config,
                contents=prompt,
            )
        except Exception as e:
            if "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
                print(
                    "  generate_content quota exhausted for today (free tier). "
                    "The index and search work; try the answer later.\n"
                )
            else:
                print(f"  I couldn't generate the answer: {str(e)[:160]}\n")
            continue

        print(f"\nAgent> {response.text}\n")
        print("  Sources used (cosine similarity):")
        for meta, distance in zip(metas, distances):
            print(f"    - {meta['source']}   (distance {distance:.3f})")
        print()


if __name__ == "__main__":
    main()