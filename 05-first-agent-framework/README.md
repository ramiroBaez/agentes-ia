# Proyecto 5 — Tu primer agente con framework (LangGraph) / Project 5 — Your first agent with a framework (LangGraph)

**🌐 Idioma / Language:** [Español](#español) · [English](#english)

---

<a id="español"></a>
## 🇪🇸 Español

### Resumen

Recrea el agente de la tienda del Proyecto 2, pero el **loop ReAct se delega a un framework** en vez de programarse a mano. Definimos el **grafo de estados** de LangGraph (nodos + ramificación condicional) y el framework maneja el loop, el límite de pasos y la **memoria de corto plazo** mediante un **checkpointer** (`MemorySaver`) identificado por `thread_id`.

Además, la llamada al modelo pasa por un **adaptador multi-proveedor** (`provider.py`): el mismo agente puede usar **Gemini**, **Groq** u **OpenRouter**, configurados como **cadena de respaldo** en el `.env` (si un proveedor falla por cuota, red o auth, el siguiente de la lista responde automáticamente).

El resultado es un **agente conversacional con memoria**, con las mismas 4 herramientas de la tienda (buscar, stock, cuentas exactas y cupones) y un flujo que **se ramifica según lo que decida el modelo**: llamar herramientas o responder directo.

### Cómo funciona

| Paso | Qué pasa |
|------|----------|
| 1 | El mensaje del usuario entra al grafo en el nodo `agent` |
| 2 | `agent` envía todo el historial al modelo (vía `provider.py`) |
| 3 | **Ramificación condicional** (`route`): si el modelo pidió herramientas → nodo `tools`; si respondió en texto → `END` |
| 4 | El nodo `tools` ejecuta cada llamada por un dispatch y devuelve los resultados al modelo |
| 5 | Un **checkpointer** (`MemorySaver`) guarda el historial por `thread_id`, así el siguiente turno conserva el contexto |

### La cadena de respaldo

`provider.py` expone un formato de mensaje canónico (dicts planos) y lo traduce a cada backend. La variable `PROVIDERS` del `.env` es una lista ordenada:

```ini
PROVIDERS=gemini,groq,openrouter
```

`provider.chat(...)` prueba cada proveedor en orden, saltea los que no tienen key, y solo lanza un error combinado si **todos** fallan. `provider.LAST_PROVIDER` te dice quién respondió en cada turno (el agente lo imprime). Para forzar un proveedor en una prueba, poné `PROVIDERS` en la línea de comandos (le gana al `.env`).

Corré `provider.diagnostics()` para ver qué proveedores quedaron configurados y con qué modelo.

### Cómo ejecutarlo

Desde la raíz del repo (tras el setup inicial del `README.md` raíz):

```bash
venv\Scripts\python.exe 05-first-agent-framework\langgraph_agent.py   # Windows
```

El `.env.example` de la raíz ya incluye keys de Groq y OpenRouter. Modelos free de OpenRouter: usá el sufijo `:free` (p. ej. `qwen/qwen3.8-27b:free`) para no gastar crédito. Si un modelo deja de andar, listá los disponibles:

```python
import os
os.environ["PROVIDERS"] = "openrouter"
import provider
print(provider.diagnostics())
print([m.id for m in provider._clients["openrouter"].models.list()])
```

Salida real (con `PROVIDERS=groq`, para ver el proveedor explícito):

```
Proveedores (con respaldo): groq
Agente de tienda online (LangGraph). ¿Qué necesitás?
Escribí 'salir' para terminar la sesión.

Vos> ¿Cuánto sale comprar 2 monitores?

  [Nodo agente] Consultando al modelo (memoria: 1 mensajes)...
  [Nodo agente] El modelo decidió: llamar herramienta(s): search_product (proveedor: groq)
  [Nodo herramientas] Ejecutando search_product con {'term': 'monitor'}
  [Nodo herramientas] Resultado: Productos encontrados:
- monitor: $249,999.00 (stock: 8)
  [Nodo agente] Consultando al modelo (memoria: 3 mensajes)...
  [Nodo agente] El modelo decidió: llamar herramienta(s): calc_total (proveedor: groq)
  [Nodo herramientas] Ejecutando calc_total con {'product': 'monitor', 'quantity': 2}
  [Nodo herramientas] Resultado: Producto: monitor
Precio unitario: $249,999.00
Cantidad: 2
Subtotal: $499,998.00
IVA (21%): $104,999.58
Total: $604,997.58
  [Nodo agente] Consultando al modelo (memoria: 5 mensajes)...
  [Nodo agente] El modelo decidió: responder en texto (proveedor: groq)
Agente> 2 monitores cuestan $604,997.58 en total (IVA 21% incluido).
```

Notá cómo **una sola pregunta dispara dos herramientas en cadena** (buscar y después calcular). El agente responde siempre en español (el prompt de sistema se lo pide): el código y las herramientas quedan en inglés, la demo en español.

### Estructura

```
05-first-agent-framework/
├── langgraph_agent.py     # el agente de la tienda: grafo de estados + memoria + herramientas
└── provider.py            # adaptador multi-proveedor (gemini | groq | openrouter)
```

### Habilidades cubiertas

- **Grafo de estados** de LangGraph: nodos (`agent`, `tools`), aristas y **ramificación condicional** (`route`)
- Mensajes con `Annotated[list, operator.add]`: LangGraph acumula, no reemplaza
- Memoria de corto plazo con `MemorySaver` + `thread_id` fijo; `recursion_limit` para limitar pasos por pregunta
- **Adaptador multi-proveedor con cadena de respaldo** para que el agente siga andando cuando un proveedor agota su cuota free tier
- Dicts de mensaje canónicos que se serializan limpio por el checkpointer (sin tipos del SDK)

---

<a id="english"></a>
## 🇬🇧 English

### Overview

Recreates the store agent from Project 2, but the **ReAct loop is delegated to a framework** instead of hand-written. We define LangGraph's **state graph** (nodes + conditional branching) and the framework handles the loop, the step limit and the **short-term memory** through a **checkpointer** (`MemorySaver`) identified by `thread_id`.

On top of that, the model call goes through a **multi-provider adapter** (`provider.py`): the same agent can use **Gemini**, **Groq** or **OpenRouter** — configured as a **failover chain** in the `.env` (if one provider fails due to quota, network or auth, the next one in the list answers automatically).

The result is a **conversational agent with memory**, the same 4 store tools (search, stock, exact money calculations and coupons) and a flow that **branches depending on what the model decides**: call tools or reply directly.

### How it works

| Step | What happens |
|------|--------------|
| 1 | The user's message enters the graph at the `agent` node |
| 2 | `agent` sends the whole history to the model (via `provider.py`) |
| 3 | **Conditional branching** (`route`): if the model requested tool calls → `tools` node; if it answered in text → `END` |
| 4 | The `tools` node executes each call through a dispatch and feeds the results back to the model |
| 5 | A **checkpointer** (`MemorySaver`) stores the history per `thread_id`, so the next turn keeps the conversation context |

### The failover chain

`provider.py` exposes a canonical message format (plain dicts) and translates it to each backend. The `PROVIDERS` env var is an ordered list:

```ini
PROVIDERS=gemini,groq,openrouter
```

`provider.chat(...)` tries each provider in order, skips the ones without a key, and raises a combined error only if **all** fail. `provider.LAST_PROVIDER` tells you who answered in each turn (the agent prints it). To force a single provider for a test run, set `PROVIDERS` on the command line (it beats the `.env`).

Run `provider.diagnostics()` to see which providers ended up configured and with which model.

### Getting started

From the repo root (after the initial setup in the root `README.md`):

```bash
venv\Scripts\python.exe 05-first-agent-framework\langgraph_agent.py   # Windows
```

The `.env` example from the repo root already includes Groq and OpenRouter keys. OpenRouter free models: use the `:free` suffix (e.g. `qwen/qwen3.8-27b:free`) so you never spend credits. If a model stops working, list the currently available ones:

```python
import os
os.environ["PROVIDERS"] = "openrouter"
import provider
print(provider.diagnostics())
print([m.id for m in provider._clients["openrouter"].models.list()])
```

Example session (run with `PROVIDERS=groq` to see the provider explicitly):

```
Proveedores (con respaldo): groq
Agente de tienda online (LangGraph). ¿Qué necesitás?
Escribí 'salir' para terminar la sesión.

Vos> ¿Cuánto sale comprar 2 monitores?

  [Nodo agente] Consultando al modelo (memoria: 1 mensajes)...
  [Nodo agente] El modelo decidió: llamar herramienta(s): search_product (proveedor: groq)
  [Nodo herramientas] Ejecutando search_product con {'term': 'monitor'}
  [Nodo herramientas] Resultado: Productos encontrados:
- monitor: $249,999.00 (stock: 8)
  [Nodo agente] Consultando al modelo (memoria: 3 mensajes)...
  [Nodo agente] El modelo decidió: llamar herramienta(s): calc_total (proveedor: groq)
  [Nodo herramientas] Ejecutando calc_total con {'product': 'monitor', 'quantity': 2}
  [Nodo herramientas] Resultado: Producto: monitor
Precio unitario: $249,999.00
Cantidad: 2
Subtotal: $499,998.00
IVA (21%): $104,999.58
Total: $604,997.58
  [Nodo agente] Consultando al modelo (memoria: 5 mensajes)...
  [Nodo agente] El modelo decidió: responder en texto (proveedor: groq)
Agente> 2 monitores cuestan $604,997.58 en total (IVA 21% incluido).
```

> Note: the demo console output is in Spanish (the repo targets Spanish-speaking audiences; the system prompt tells the model to answer in Spanish), but the code, identifiers and docstrings stay in English — the industry standard.

Note how **one question triggers two chained tool calls** (search, then calculate), and the follow-up question is answered **from memory** (no tools needed).

### Labels

```
05-first-agent-framework/
├── langgraph_agent.py     # the store agent: state graph + memory + tools
└── provider.py            # multi-provider adapter (gemini | groq | openrouter)
```

### Skills covered

- A LangGraph **state graph**: nodes (`agent`, `tools`), edges and **conditional branching** (`route`)
- `Annotated[list, operator.add]` messages: LangGraph accumulates, it doesn't replace
- Short-term memory with `MemorySaver` + fixed `thread_id`; `recursion_limit` to cap steps per question
- A **multi-provider adapter with failover chain** so the agent keeps working when a provider hits its free-tier quota
- Canonical message dicts that serialize cleanly through the checkpointer (no SDK types leaked)