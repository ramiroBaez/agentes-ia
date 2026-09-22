# Proyecto 6 — RAG dentro de un agente con framework (LangGraph) / Project 6 — RAG inside a framework agent (LangGraph)

**🌐 Idioma / Language:** [Español](#español) · [English](#english)

---

<a id="español"></a>
## 🇪🇸 Español

### Resumen

Este proyecto no crea un agente nuevo: **combina el Proyecto 5 y el Proyecto 4**. Al agente de la tienda (grafo de LangGraph con memoria) le agregamos el RAG como **una herramienta más**: `search_notes` recupera de las notas del curso de agentes los chunks más parecidos a la pregunta, y el modelo decide *él mismo* cuándo consultarlas — nadie lo fuerza.

Es la diferencia clave con el Proyecto 4: allá el RAG era **todo el programa** (cada pregunta pasaba sí o sí por la búsqueda); acá es **una capability más**, y el loop ReAct del framework orquesta cuándo buscar en el catálogo, cuándo calcular y cuándo recuperar conocimiento.

Reutiliza la **misma base de conocimiento e índice que el Proyecto 4** (`04-sistema-rag/notas/` → colección ChromaDB `notas_agentes`), así que no se indexa nada dos veces.

### Cómo funciona

| Paso | Qué pasa |
|------|----------|
| 1 | El mensaje del usuario entra al grafo en el nodo `agent` |
| 2 | `agent` envía todo el historial al modelo (vía `provider.py`, cadena de respaldo) |
| 3 | **Ramificación condicional**: si el modelo pidió herramientas → nodo `tools`; si respondió en texto → `END` |
| 4 | El nodo `tools` ejecuta cada tool: acciones de la tienda (buscar, stock, cuentas) **o** `search_notes` (RAG sobre las notas) |
| 5 | Los resultados vuelven al modelo, que arma la respuesta final **citando los chunks recuperados** |
| 6 | Un **checkpointer** (`MemorySaver`) mantiene el contexto de la conversación entre turnos |

### Las herramientas

| Herramienta | Dominio | De dónde sale |
|---|---|---|
| `search_product` | tienda | Proyecto 2/5 |
| `check_stock` | tienda | Proyecto 2/5 |
| `calc_total` | tienda (cuentas) | Proyecto 2/5 |
| `apply_coupon` | tienda | Proyecto 2/5 |
| `search_notes` | conocimiento (**RAG**) | nueva · Proyecto 4 |

`search_notes` devuelve los top-4 chunk con su archivo y distancia coseno, para que el modelo responda **anclado** en las notas:

```
[05-mcp.md] (distancia 0.242)
MCP works with a two-part architecture: ...
```

> **Nota sobre embeddings:** `search_notes` usa embeddings de Gemini (`gemini-embedding-001`) a través del mismo `rag` que el Proyecto 4. No pasan por la cadena `provider` — la recuperación necesita `GEMINI_API_KEY` aunque el chat responda por Groq/OpenRouter.

### Cómo ejecutarlo

Desde la raíz del repo (tras el setup inicial del `README.md` raíz):

```bash
venv\Scripts\python.exe 06-rag-inside-framework\rag_agent.py            # Windows — índice existente
venv\Scripts\python.exe 06-rag-inside-framework\rag_agent.py --reindex  # reconstruir el índice
```

Salida real (respondió Groq vía cadena de respaldo):

```
Proveedores (con respaldo): gemini, groq, openrouter
Agente de tienda + notas (LangGraph + RAG). ¿Qué necesitás?
Ej.: '¿Qué es MCP?' · '¿cuánto cuestan 2 monitores?'

Vos> ¿Qué es MCP?

  [Nodo agente] El modelo decidió: llamar herramienta(s): search_notes (proveedor: groq)
  [Nodo herramientas] Ejecutando search_notes con {'query': 'MCP'}
  [Nodo herramientas] Resultado: [05-mcp.md] (distancia 0.305)
  [Nodo agente] El modelo decidió: responder en texto (proveedor: groq)
Agente> MCP (Model Context Protocol) es un estándar abierto creado por Anthropic
que define una única manera de exponer herramientas y datos a los agentes de IA...
[responde citando los chunks recuperados]

Vos> ¿Cuánto cuesta comprar 2 monitores?

  [Nodo agente] El modelo decidió: llamar herramienta(s): search_product (proveedor: groq)
  [Nodo herramientas] Ejecutando search_product con {'term': 'monitor'}
  [Nodo agente] El modelo decidió: llamar herramienta(s): calc_total (proveedor: groq)
Agente> El total de 2 monitores es $604,997.58 (IVA 21% incluido).

Vos> ¿Y cuál era el total que te pasé recién?

  [Nodo agente] El modelo decidió: responder en texto (proveedor: groq)
Agente> El total que te pasé era $604,997.58.
```

El agente responde siempre en español (el prompt de sistema se lo pide). La misma sesión mezcla los dos dominios y **el modelo elige solo**, y el último turno se responde **desde la memoria**.

### Estructura

```
06-rag-inside-framework/
├── rag_agent.py     # el agente: grafo de estados + memoria + tools de tienda + search_notes
├── rag.py           # recuperación RAG sobre el índice del Proyecto 4 (ChromaDB compartida)
└── provider.py      # adaptador multi-proveedor (el mismo del Proyecto 5)
```

### Habilidades cubiertas

- RAG como **herramienta que el modelo decide llamar**, dentro de un agente LangGraph (vs. Proyecto 4, donde el RAG era todo el programa)
- Reutilización de un índice compartido entre proyectos (misma colección `notas_agentes` de ChromaDB)
- Un solo prompt que gobierna **dos dominios** (acciones de tienda + recuperación de conocimiento)
- Todo lo del Proyecto 5: grafo de estados, ramificación condicional, memoria corto plazo con `MemorySaver`, `recursion_limit`

---

<a id="english"></a>
## 🇬🇧 English

### Overview

This project does not create a new agent: it **combines Project 5 and Project 4**. The store agent (LangGraph graph with memory) gains RAG as **just another tool**: `search_notes` retrieves the chunks of the agent-course notes most similar to the question, and the model decides *itself* when retrieval is needed — nobody forces it.

That is the key difference with Project 4: there, RAG was **the whole program** (every question went through the search); here RAG is **one capability among others**, and the framework's ReAct loop orchestrates when to search the catalog, when to calculate, and when to retrieve knowledge.

It reuses the **same knowledge base and index as Project 4** (`04-sistema-rag/notas/` → ChromaDB collection `notas_agentes`), so nothing is indexed twice.

### How it works

| Step | What happens |
|------|--------------|
| 1 | The user's message enters the graph at the `agent` node |
| 2 | `agent` sends the whole history to the model (via `provider.py`, failover chain) |
| 3 | **Conditional branching**: if the model requested tools → `tools` node; if it answered in text → `END` |
| 4 | The `tools` node runs each tool: store actions (search, stock, money) **or** `search_notes` (RAG over the notes) |
| 5 | Results go back to the model, which composes the final answer **citing the retrieved chunks** |
| 6 | A **checkpointer** (`MemorySaver`) keeps the conversation context between turns |

### The tools

| Tool | Domain | Comes from |
|---|---|---|
| `search_product` | store | Project 2/5 |
| `check_stock` | store | Project 2/5 |
| `calc_total` | store (money) | Project 2/5 |
| `apply_coupon` | store | Project 2/5 |
| `search_notes` | knowledge (**RAG**) | new · Project 4 |

`search_notes` returns the top-4 chunks with their file name and cosine distance, so the model answers **grounded** in the notes:

```
[05-mcp.md] (distance 0.242)
MCP works with a two-part architecture: ...
```

> **Note on embeddings:** `search_notes` uses Gemini embeddings (`gemini-embedding-001`) through the same `rag` module as Project 4. They do **not** go through the `provider` failover chain — so retrieval needs `GEMINI_API_KEY` even if the chat answers via Groq/OpenRouter.

### Getting started

From the repo root (after the initial setup in the root `README.md`):

```bash
venv\Scripts\python.exe 06-rag-inside-framework\rag_agent.py            # Windows — existing index
venv\Scripts\python.exe 06-rag-inside-framework\rag_agent.py --reindex  # rebuild the index
```

Example session (the failover chain answered via Groq):

```
Proveedores (con respaldo): gemini, groq, openrouter
Agente de tienda + notas (LangGraph + RAG). ¿Qué necesitás?
Ej.: '¿Qué es MCP?' · '¿cuánto cuestan 2 monitores?'

Vos> ¿Qué es MCP?

  [Nodo agente] El modelo decidió: llamar herramienta(s): search_notes (proveedor: groq)
  [Nodo herramientas] Ejecutando search_notes con {'query': 'MCP'}
  [Nodo herramientas] Resultado: [05-mcp.md] (distancia 0.305)
  [Nodo agente] El modelo decidió: responder en texto (proveedor: groq)
Agente> MCP (Model Context Protocol) es un estándar abierto creado por Anthropic
que define una única manera de exponer herramientas y datos a los agentes de IA...
[responde citando los chunks recuperados]

Vos> ¿Cuánto cuesta comprar 2 monitores?

  [Nodo agente] El modelo decidió: llamar herramienta(s): search_product (proveedor: groq)
  [Nodo herramientas] Ejecutando search_product con {'term': 'monitor'}
  [Nodo agente] El modelo decidió: llamar herramienta(s): calc_total (proveedor: groq)
Agente> El total de 2 monitores es $604,997.58 (IVA 21% incluido).

Vos> ¿Y cuál era el total que te pasé recién?

  [Nodo agente] El modelo decidió: responder en texto (proveedor: groq)
Agente> El total que te pasé era $604,997.58.
```

> Note: the demo console output is in Spanish (the repo targets Spanish-speaking audiences; the system prompt tells the model to answer in Spanish), but the code, identifiers and docstrings stay in English — the industry standard.

The same session mixes both domains and **the model picks on its own**, plus the last turn is answered **from memory**.

### Labels

```
06-rag-inside-framework/
├── rag_agent.py     # the agent: state graph + memory + store tools + search_notes
├── rag.py           # RAG retrieval over Project 4's index (shared ChromaDB)
└── provider.py      # multi-provider adapter (same one as Project 5)
```

### Skills covered

- Adding RAG as a **tool the model decides to call**, inside a LangGraph agent (vs. Project 4 where RAG was the whole program)
- Reusing a shared index across projects (same ChromaDB `notas_agentes` collection)
- A single prompt that governs **two domains** (store actions + knowledge retrieval)
- Everything from Project 5: state graph, conditional branching, `MemorySaver` short-term memory, `recursion_limit`