# Project 4 — Mini RAG System / Proyecto 4 — Mini sistema RAG

**🌐 Language / Idioma:** [English](#english) · [Español](#español)

---

<a id="english"></a>
## 🇬🇧 English

### Overview

A mini **RAG (Retrieval-Augmented Generation)** system: it indexes Markdown documents into a ChromaDB vector database and answers questions about them, grounding its answers in the real information of those documents instead of the model's memory.

It comes with a curated built-in knowledge base in `notas/` about the AI agents stack (function calling, structured outputs, RAG, MCP, production & security) — and you can drop your own `.md` files in there.

### How it works

| Step | What happens |
|------|--------------|
| 1 | **Chunking** — documents are split into ~800-char pieces with 150-char overlap |
| 2 | **Embeddings** — every chunk is converted into a vector with `gemini-embedding-001` |
| 3 | **Storage** — vectors are saved in a persistent **ChromaDB** collection (cosine space) |
| 4 | **Search** — the question is embedded and the top-5 most similar chunks are retrieved |
| 5 | **Generation** — those chunks go into the prompt as the *only* allowed context; the model answers citing the sources used |

### Getting started

From the repo root (after the initial setup in the root `README.md`):

```bash
venv\Scripts\python.exe 04-sistema-rag\rag_chat.py   # Windows
```

The first run indexes the `notas/` folder automatically. To rebuild the index from scratch:

```bash
venv\Scripts\python.exe 04-sistema-rag\rag_chat.py --reindex
```

Example session:

```
Question> What is a ReAct loop?

Agent> The core loop of an AI agent is known as **ReAct (Reason + Act)**. In this
loop, the model reasons about what to do, acts through a tool, and incorporates
the result into its next decision.

  Sources used (cosine similarity):
    - 01-agents.md   (distance 0.351)
    - 02-function-calling.md   (distance 0.412)
    ...
```

### Adding your own documents

Put any `.md` file inside `04-sistema-rag/notas/` and run `--reindex`. Query in the language the documents are written in for best results.

> **Free-tier note:** the free Gemini tier rate-limits requests. The script batches embeddings and, on a `429`/`RESOURCE_EXHAUSTED`, waits the exact delay the API suggests before retrying — it never crashes on quota errors.

### Labels

```
04-sistema-rag/
├── rag_chat.py          # Chunk, embed, index, search and chat
├── notas/               # Knowledge base (add your own docs here)
└── chroma_db/           # Vector DB — auto-generated, git-ignored
```

### Skills covered

- The full RAG pipeline: chunking → embeddings → vector DB → semantic search → context injection
- `chromadb.PersistentClient` and a cosine-similarity collection
- Rate-limit-aware embedding batching (respects the API's suggested retry)
- Grounding: a system instruction that allows the model to use ONLY the retrieved context
- Source attribution per answer (file + cosine distance)

---

<a id="español"></a>
## 🇪🇸 Español

### Resumen

Un **mini sistema RAG (Retrieval-Augmented Generation)**: indexa documentos Markdown en una base vectorial ChromaDB y responde preguntas sobre ellos, anclando sus respuestas en la información real de esos documentos en vez de la memoria del modelo.

Viene con una base de conocimiento curada incluida en `notas/` sobre el stack de agentes de IA (function calling, salidas estructuradas, RAG, MCP, producción y seguridad) — y podés agregar tus propios archivos `.md` ahí.

### Cómo funciona

| Paso | Qué pasa |
|------|----------|
| 1 | **Chunking** — los documentos se cortan en pedazos de ~800 caracteres con 150 de solape |
| 2 | **Embeddings** — cada chunk se convierte en un vector con `gemini-embedding-001` |
| 3 | **Almacenamiento** — los vectores se guardan en una colección persistente de **ChromaDB** (espacio coseno) |
| 4 | **Búsqueda** — la pregunta se embediea y se recuperan los 5 chunks más parecidos |
| 5 | **Generación** — esos chunks entran al prompt como el *único* contexto permitido; el modelo responde citando las fuentes usadas |

### Cómo ejecutarlo

Desde la raíz del repo (tras el setup inicial del `README.md` raíz):

```bash
venv\Scripts\python.exe 04-sistema-rag\rag_chat.py   # Windows
```

La primera corrida indexa la carpeta `notas/` automáticamente. Para reconstruir el índice desde cero:

```bash
venv\Scripts\python.exe 04-sistema-rag\rag_chat.py --reindex
```

### Agregar tus propios documentos

Poné cualquier archivo `.md` dentro de `04-sistema-rag/notas/` y corré `--reindex`. Consultá en el idioma en que están escritos los documentos para mejores resultados.

> **Nota del free tier:** el tier gratuito de Gemini limita las requests. El script agrupa los embeddings y, ante un `429`/`RESOURCE_EXHAUSTED`, espera exactamente el tiempo que la API sugiere antes de reintentar — nunca crashea por cuota.

### Estructura

```
04-sistema-rag/
├── rag_chat.py          # Chunk, embed, index, search and chat
├── notas/               # Base de conocimiento (acá van tus docs)
└── chroma_db/           # Base vectorial — auto-generada, git-ignored
```

### Habilidades cubiertas

- Pipeline RAG completo: chunking → embeddings → base vectorial → búsqueda semántica → inyección de contexto
- `chromadb.PersistentClient` y colección con similitud coseno
- Batching de embeddings consciente de rate limits (respeta el retry sugerido por la API)
- Grounding: instrucción de sistema que permite al modelo usar SOLO el contexto recuperado
- Atribución de fuentes por respuesta (archivo + distancia coseno)