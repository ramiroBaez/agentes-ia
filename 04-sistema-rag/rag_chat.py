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
import sys
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
    print("Error: definí GEMINI_API_KEY en tu archivo .env para llamar a la API.")
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
    "is not in the context, say so clearly. Answer in Spanish, since the user "
    "and the knowledge base are in Spanish."
)


# ------------------------------------------------------------ API handling
def call_with_retries(func, *args, **kwargs):
    """Call func with retries; if the API asks to wait (429), respect that time."""
    delay = 1
    for attempt in range(1, 4):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            print(f"  [Error en la llamada (intento {attempt}/3): {str(e)[:110]}]")
            if attempt == 3:
                raise
            if "retry in" in str(e):
                pause = _retry_seconds(e)
            else:
                pause = delay
                delay *= 2
            print(f"  Reintentando en {pause:.0f}s...")
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
                    f"  [Lote {len(vectors) // EMBEDDING_BATCH + 1}: error "
                    f"(intento {attempt}/5): {str(e)[:90]}]"
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
    print("Indexando tu base de conocimiento...")
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
            f"Usando el índice existente ({collection.count()} fragmentos). "
            "Pasá --reindex para reconstruirlo.\n"
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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Mini sistema RAG sobre tu base de conocimiento de Agentes IA."
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="Reconstruir el índice desde cero antes de chatear.",
    )
    args = parser.parse_args()

    collection = get_collection(args.reindex)

    answer_config = types.GenerateContentConfig(
        system_instruction=ANSWER_SYSTEM_INSTRUCTION,
    )

    print("Chat RAG sobre tu base de conocimiento.")
    print("Escribí una pregunta (o 'salir' para terminar).\n")

    while True:
        question = input("Pregunta> ").strip()
        if not question:
            continue
        if question.lower() in ("salir", "exit", "quit", "q"):
            print("¡Hasta la próxima!")
            break

        print("  [Buscando en tus documentos...]")
        results = search(collection, question)
        context = build_context(results)
        metas = results["metadatas"][0]
        distances = results["distances"][0]

        prompt = (
            "Contexto (unica información que podés usar):\n"
            f"{context}\n\n"
            f"Pregunta del usuario: {question}"
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
                    "  Cuota de generate_content agotada por hoy (free tier). "
                    "El índice y la búsqueda funcionan; probá la respuesta más tarde.\n"
                )
            else:
                print(f"  No pude generar la respuesta: {str(e)[:160]}\n")
            continue

        print(f"\nAgente> {response.text}\n")
        print("  Fuentes usadas (similitud coseno):")
        for meta, distance in zip(metas, distances):
            print(f"    - {meta['source']}   (distancia {distance:.3f})")
        print()


if __name__ == "__main__":
    main()