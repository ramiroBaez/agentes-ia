r"""User knowledge: semantic search over the notes (adapted from Project 6).

The `search_notes` tool from Project 6, with the same Chroma indexing (Project 4,
04-sistema-rag) and the same output format (fragment + source + distance), so
the agent can cite the real course notes instead of making things up.

In Project 12 this tool also introduces the most prompt-injection risk (the
retrieved text becomes model context): that is why `agent.py` runs embedded-
instruction detection on whatever it returns here.
"""

import logging
import threading

from google.genai import types

import rag

# The collection is injected when the app starts (keeps the module independent
# of the FastAPI lifecycle and testable with a stand-in).
ACTIVE_COLLECTION = None

TOP_K = 4

_lock = threading.Lock()
_thread = None
_index_error = ""


def start_index_in_background() -> None:
    """Start indexing in a separate thread, only once.

    Indexing means Gemini embeddings over all the notes: it takes a while. Done
    at startup, the API would not come up until it finished; done on the first
    turn, the user would wait until a timeout. In a thread the API comes up
    anyway and the first query says the index is being prepared.
    """
    global _thread
    with _lock:
        if ACTIVE_COLLECTION is not None or (_thread is not None and _thread.is_alive()):
            return
        _thread = threading.Thread(target=_build_index, name="rag-index", daemon=True)
        _thread.start()


def _build_index() -> None:
    global ACTIVE_COLLECTION, _index_error
    try:
        ACTIVE_COLLECTION = rag.get_collection()
    except Exception as e:  # no API key, no notes, Chroma down: the turn must not break
        _index_error = str(e)
        logging.getLogger(__name__).warning("no se pudo preparar el índice de notas: %s", e)


def index_ready() -> bool:
    return ACTIVE_COLLECTION is not None and ACTIVE_COLLECTION.count() > 0


def search_notes(query: str) -> str:
    """Search the notes for the query and return the most relevant fragments."""
    if ACTIVE_COLLECTION is None:
        start_index_in_background()
        reason = f" ({_index_error})" if _index_error else ""
        return (
            "Estoy preparando el índice de las notas, todavía no está listo"
            f"{reason}. Probá de nuevo en un momento."
        )
    if not rag.has_notes_to_index():
        return "No hay notas del curso indexadas en este entorno."
    try:
        results = rag.retrieve(ACTIVE_COLLECTION, query, top_k=TOP_K)
    except Exception as e:
        return f"Error al buscar en las notas: {str(e)[:150]}"
    if not results:
        return "No encontré nada en las notas con esa consulta."
    return "\n\n---\n\n".join(
        f"[{r['source']}] (distancia {r['distance']:.3f})\n{r['text']}"
        for r in results
    )


declaration_notes = types.FunctionDeclaration(
    name="search_notes",
    description=(
        "Busca en las notas de estudio del curso de Agentes de IA del usuario "
        "y devuelve los fragmentos más relevantes con su fuente. Usala cuando "
        "la pregunta sea sobre teoría de agentes: fases del plan, loop ReAct, "
        "RAG, MCP, prompt engineering, frameworks, producción, seguridad, "
        "multi-agente... (ej. '¿qué es MCP?', 'explicame el loop ReAct', "
        "'qué cubre la fase de seguridad')."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "La pregunta o tema a buscar dentro de las notas.",
            },
        },
        "required": ["query"],
    },
)
