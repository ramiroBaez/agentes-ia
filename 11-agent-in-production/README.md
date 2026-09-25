# Proyecto 11 — Poner un agente en producción (FastAPI + Docker) / Project 11 — Agent in production (FastAPI + Docker)

**🌐 Idioma / Language:** [Español](#español) · [English](#english)

---

<a id="español"></a>
## 🇪🇸 Español

### Resumen

Es el salto de la **consola al servicio**: tomamos el agente más completo de la serie (Proyecto 6: tienda online + RAG con LangGraph) y lo exponemos como una **API HTTP** con FastAPI. El agente **no se reescribe**: se reutiliza tal cual (el módulo `rag_agent` se vende dentro de esta carpeta) — solo cambia la *puerta de entrada* (input de consola → request/response HTTP) y se le agrega la capa de producción:

- **Validación** de entrada/salida con Pydantic
- Endpoint de **salud** (`/health`) sin gastar tokens
- **Logging** por cada request (`session`, proveedor, pasos, duración)
- **Docker**: imagen + `docker-compose` con el índice RAG montado como volumen

Ahora cualquier sistema externo puede usarlo: una app web, un bot de WhatsApp/Telegram, un flujo de n8n, etc. La memoria es **por sesión** (LangGraph `MemorySaver`, en el proceso): mismo `session` = el agente se acuerda; sin `session`, se crea una conversación nueva y el ID viaja en la respuesta para continuarla.

### Cómo funciona

| Paso | Qué pasa |
|------|----------|
| 1 | El cliente hace `POST /ask` con `{"question", "session"}` |
| 2 | Pydantic valida la entrada (longitudes, tipos) |
| 3 | El agente corre por LangGraph: decide solo si usa la tienda, el RAG o responde directo |
| 4 | Se registra el log de la ejecución (proveedor, pasos, duración) |
| 5 | La respuesta sale validada: `{"answer", "session", "provider", "steps"}` |

### Endpoints

| Método | Ruta | Descripción | Cuerpo |
|---|---|---|---|
| `GET` | `/` | Info de la API + proveedores | — |
| `GET` | `/health` | Readiness (proveedores + RAG), sin tokens | — |
| `POST` | `/ask` | Preguntarle al agente | `{"question": str (1–4000), "session": str? (≤128)}` |
| `GET` | `/docs` | Swagger UI interactivo | — |

Respuesta de `POST /ask`:

```json
{
  "answer": "El total de 2 monitores es $604,997.58 (IVA 21% incluido).",
  "session": "mi-sesion",
  "provider": "groq",
  "steps": 2
}
```

### Cómo ejecutarlo

Requisitos: Python 3.11+ (local) o Docker (contenedor), y el setup inicial del `README.md` raíz (`.env` con las API keys).

#### Local

```bash
# desde la raíz del repo
venv\Scripts\python.exe -m uvicorn 11-agent-in-production.api:app --host 127.0.0.1 --port 8000
# o directo (equivale a uvicorn api:app sobre 0.0.0.0:8000)
venv\Scripts\python.exe 11-agent-in-production\api.py
```

> Antes de la primera consulta el agente arma el grafo y carga el índice RAG (compartido con el Proyecto 4, en `04-sistema-rag/chroma_db`). Si el índice no existe, lo construye desde `04-sistema-rag/notas`.

#### Docker

```bash
docker compose -f 11-agent-in-production/docker-compose.yml up --build
```

El compose monta `04-sistema-rag/chroma_db` (escribible — ChromaDB escribe su lock/WAL incluso al leer: montarlo `:ro` falla con `attempt to write a readonly database`) y `04-sistema-rag/notas` (solo lectura). La imagen no incluye tu índice ni tus keys (van por `env_file: ../.env`).

#### Probar con curl

PowerShell rompe el JSON en línea al pasárselo a `curl.exe` (pierde las comillas dobles), por eso usamos un archivo de cuerpo — igual que en el Proyecto 10:

```bash
'{"question":"tenes auriculares bluetooth?","session":"mi-sesion"}' | Set-Content -Encoding UTF8 body.json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/ask -ContentType "application/json" -Body (Get-Content -Raw body.json)
# o en bash / WSL / Linux:
# curl -s -X POST http://127.0.0.1:8000/ask -H "Content-Type: application/json" -d @body.json
```

Salida real (respondió Groq vía cadena de respaldo):

```
answer    : No tengo "auriculares bluetooth" con ese nombre exacto, pero sí
            tengo "bluetooth headphones" a $12,999.99 (12 unidades en stock).
            ¿Querés que te arme una compra?
session   : mi-sesion
provider  : groq
steps     : 2
```

Para ver la **memoria por sesión**: mandá una segunda pregunta con el mismo `session` (p. ej. "¿y cuánto salen 2?"), y el agente recuerda de qué estaban hablando.

**Pregunta con RAG:** `{"question":"¿qué es MCP?","session":"mi-sesion"}` — el agente usa la herramienta `search_notes` sobre las notas del curso y responde citando los chunks.

### Prueba rápida

```bash
Invoke-RestMethod http://127.0.0.1:8000/health
```

Debe devolver `status: ok`, `rag_available: True` (si el índice existe) y el/los `providers` configurados.

### Deploy en la nube

El Dockerfile expone el puerto 8000 y lee toda la config desde variables de entorno, así que sube a cualquier plataforma PaaS que corra contenedores (Render, Railway, Fly.io, Google Cloud Run, AWS ECS...):

1. Apuntá el build al repo (el `Dockerfile` de `11-agent-in-production/`).
2. Copiá las variables de tu `.env` local a los secretos/variables de la plataforma.
3. **RAG**: montá (o subí) `04-sistema-rag/notas` dentro de `04-sistema-rag/notas` y exponé un volumen persistente en `04-sistema-rag/chroma_db` — el índice se construye solo a la primera; si no persistís ChromaDB, se re-indexa en cada boot.
4. Escalá y monitoreá con los logs del servicio.

> La memoria por sesión vive **en el proceso**: si corrés más de una réplica, el historial no viaja entre ellas. Para memoria persistente, el siguiente paso natural es un checkpointer externo (Postgres/Redis) — ver Fase 12.

### Estructura

```
11-agent-in-production/
├── api.py            # la capa HTTP: FastAPI + Pydantic + endpoints / y /health y /ask
├── provider.py       # adaptador multi-proveedor (vendado, el mismo del P5/P6)
├── rag.py            # recuperación RAG sobre 04-sistema-rag (vendado, el mismo del P6)
├── rag_agent.py      # el agente P6 reutilizado TAL CUAL (tienda + search_notes, LangGraph)
├── Dockerfile        # imagen de producción (contexto: raíz del repo)
└── docker-compose.yml# puerto 8000, env del .env raíz, volúmenes de notas (ro) + chroma_db (rw)
```

### Errores típicos

| Error / síntoma | Qué significa | Cómo resolverlo |
|---|---|---|
| `732: Unexpected token` al hacer POST /ask | El JSON en línea se rompió en PowerShell | Mandar el cuerpo desde un archivo (`-Body (Get-Content -Raw body.json)`) o usar `Invoke-RestMethod` |
| `/health` da `503` | El agente todavía se está iniciando (arma grafo + RAG al boot) | Esperar unos segundos; si persiste, mirá los logs del arranque |
| `rag_available: False` | No hay índice o no cargó el de `04-sistema-rag` | Corrés el Proyecto 4/6 una vez local (genera `chroma_db`), o verificá que el volumen esté montado en Docker |
| `attempt to write a readonly database` | `chroma_db` montado en solo lectura | El volumen de `chroma_db` debe ser escribible (ChromaDB escribe su lock/WAL incluso al leer) |
| `/health` devuelve campos en español (`estado`, `proveedores`) | Estás peguiando al `proyecto11` local, que ocupa el puerto 8000 | Paralo o mapeá el contenedor a otro puerto (p. ej. `8001:8000`) |
| Respuesta dice "no pude armarte una respuesta" | Todos los proveedores fallaron (cuota 429, red, auth) | Chequear las keys en `.env` y el orden de `PROVIDERS`; el fallback ya lo maneja `provider` |
| La memoria no persiste al reiniciar | `MemorySaver` es en memoria, por diseño | Esperado en esta fase; para persistencia real, checkpointer externo (ver Fase 12) |

### Habilidades cubiertas

- Exponer un agente LangGraph completo como **API REST** sin tocar el agente
- **Capa de producción**: validación Pydantic, endpoint de salud, logging por request, límites (longitud, `recursion_limit`)
- **Docker**: `Dockerfile` reproducible + `docker compose` con secretos fuera de la imagen y datos RAG como volúmenes
- **Depuración real**: del JSON que rompe PowerShell a fallos de proveedores y RAG ausente

---

<a id="english"></a>
## 🇬🇧 English

### Overview

This is the jump from the **console to a service**: we take the most complete agent of the series (Project 6: online store + RAG on LangGraph) and expose it as an **HTTP API** with FastAPI. The agent is **not rewritten**: it is reused as is (the `rag_agent` module is vendored into this folder) — only the *entry point* changes (console input → HTTP request/response) and a production layer is added:

- **Validation** of input/output with Pydantic
- **Health** endpoint (`/health`) without spending tokens
- **Logging** per request (`session`, provider, steps, duration)
- **Docker**: image + `docker-compose` with the RAG index mounted as a volume

Now any external system can use it: a web app, a WhatsApp/Telegram bot, an n8n workflow, etc. Memory is **per session** (LangGraph `MemorySaver`, in-process): same `session` = the agent remembers; no `session` = a new conversation is created and the ID travels back in the response to continue it.

### How it works

| Step | What happens |
|------|--------------|
| 1 | The client does `POST /ask` with `{"question", "session"}` |
| 2 | Pydantic validates the input (lengths, types) |
| 3 | The agent runs through LangGraph: it decides on its own whether to use the store, RAG or answer directly |
| 4 | The run is logged (provider, steps, duration) |
| 5 | The response comes out validated: `{"answer", "session", "provider", "steps"}` |

### Endpoints

| Method | Path | Description | Body |
|---|---|---|---|
| `GET` | `/` | API info + providers | — |
| `GET` | `/health` | Readiness (providers + RAG), no tokens | — |
| `POST` | `/ask` | Ask the agent | `{"question": str (1–4000), "session": str? (≤128)}` |
| `GET` | `/docs` | Interactive Swagger UI | — |

`POST /ask` response:

```json
{
  "answer": "El total de 2 monitores es $604,997.58 (IVA 21% incluido).",
  "session": "mi-sesion",
  "provider": "groq",
  "steps": 2
}
```

### Getting started

Requirements: Python 3.11+ (local) or Docker (container), and the initial setup from the root `README.md` (`.env` with the API keys).

#### Local

```bash
# from the repo root
venv\Scripts\python.exe -m uvicorn 11-agent-in-production.api:app --host 127.0.0.1 --port 8000
# or direct (equivalent to uvicorn api:app on 0.0.0.0:8000)
venv\Scripts\python.exe 11-agent-in-production\api.py
```

> On first request the agent builds the graph and loads the RAG index (shared with Project 4, at `04-sistema-rag/chroma_db`). If the index does not exist, it is built from `04-sistema-rag/notas`.

#### Docker

```bash
docker compose -f 11-agent-in-production/docker-compose.yml up --build
```

The compose mounts `04-sistema-rag/chroma_db` as a writable volume (ChromaDB writes its lock/WAL even when only reading: a `:ro` mount fails with `attempt to write a readonly database`) and `04-sistema-rag/notas` as read-only. The image does not include your index or your keys (they come via `env_file: ../.env`).

#### Testing with curl

This repo targets Spanish demos; the sample session below runs in Spanish. PowerShell mangles inline JSON passed to `curl.exe` (it strips the double quotes), so we send the body from a file — same trick as Project 10:

```bash
'{"question":"tenes auriculares bluetooth?","session":"mi-sesion"}' | Set-Content -Encoding UTF8 body.json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/ask -ContentType "application/json" -Body (Get-Content -Raw body.json)
# or in bash / WSL / Linux:
# curl -s -X POST http://127.0.0.1:8000/ask -H "Content-Type: application/json" -d @body.json
```

To see **per-session memory**: send a second question with the same `session` (e.g. "¿y cuánto salen 2?"), and the agent remembers what you were talking about.

**RAG question:** `{"question":"¿qué es MCP?","session":"mi-sesion"}` — the agent uses the `search_notes` tool over the course notes and answers citing the retrieved chunks.

### Quick check

```bash
Invoke-RestMethod http://127.0.0.1:8000/health
```

Should return `status: ok`, `rag_available: True` (if the index exists) and the configured `providers`.

### Cloud deployment

The Dockerfile exposes port 8000 and reads all config from environment variables, so it deploys to any PaaS that runs containers (Render, Railway, Fly.io, Google Cloud Run, AWS ECS...):

1. Point the build at the repo (the `11-agent-in-production/` `Dockerfile`).
2. Copy the variables from your local `.env` into the platform's secrets/variables.
3. **RAG**: mount (or upload) `04-sistema-rag/notas` under `04-sistema-rag/notas` and expose a persistent volume at `04-sistema-rag/chroma_db` — the index builds itself on first boot; without a persistent ChromaDB it re-indexes every start.
4. Scale and monitor with the service logs.

> Per-session memory lives **in the process**: with more than one replica the history does not travel across them. For persistent memory the natural next step is an external checkpointer (Postgres/Redis) — see Phase 12.

### Labels

```
11-agent-in-production/
├── api.py                 # the HTTP layer: FastAPI + Pydantic + /, /health and /ask endpoints
├── provider.py            # multi-provider adapter (vendored, same as P5/P6)
├── rag.py                 # RAG retrieval over 04-sistema-rag (vendored, same as P6)
├── rag_agent.py           # the Project 6 agent reused AS IS (store + search_notes, LangGraph)
├── Dockerfile             # production image (context: repo root)
└── docker-compose.yml     # port 8000, env from root .env, notes (ro) + chroma_db (rw) volumes
```

### Common issues

| Error / symptom | What it means | How to fix it |
|---|---|---|
| `732: Unexpected token` on POST /ask | The inline JSON broke in PowerShell | Send the body from a file (`-Body (Get-Content -Raw body.json)`) or use `Invoke-RestMethod` |
| `/health` returns `503` | The agent is still starting (builds graph + RAG on boot) | Wait a few seconds; if it persists, check the startup logs |
| `rag_available: False` | No index or the `04-sistema-rag` one did not load | Run Project 4/6 once locally (generates `chroma_db`), or verify the volume is mounted in Docker |
| `attempt to write a readonly database` | `chroma_db` mounted read-only | The `chroma_db` volume must be writable (ChromaDB writes its lock/WAL even when only reading) |
| `/health` returns Spanish fields (`estado`, `proveedores`) | You are hitting the local `proyecto11`, which owns port 8000 | Stop it, or map the container to another port (e.g. `8001:8000`) |
| The answer says "could not build an answer" | All providers failed (429 quota, network, auth) | Check the keys in `.env` and the `PROVIDERS` order; the fallback is already handled by `provider` |
| Memory resets on restart | `MemorySaver` is in-memory, by design | Expected at this phase; for real persistence use an external checkpointer (see Phase 12) |

### Skills covered

- Exposing a full LangGraph agent as a **REST API** without touching the agent
- **Production layer**: Pydantic validation, health endpoint, per-request logging, limits (length, `recursion_limit`)
- **Docker**: reproducible `Dockerfile` + `docker compose` with secrets out of the image and RAG data as volumes
- **Real-world debugging**: from PowerShell-mangled JSON to provider failures and a missing RAG index