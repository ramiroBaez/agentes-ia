# Proyecto 12 — Observabilidad y seguridad (Aurora Shop Ops) / Project 12 — Observability & security (Aurora Shop Ops)

**🌐 Idioma / Language:** [Español](#español) · [English](#english)

---

<a id="español"></a>
## 🇪🇸 Español

### Resumen

Es el salto de "un agente que anda" a "un agente que se puede **operar**": un agente de e-commerce (consulta el catálogo, mira pedidos, emite reembolsos y busca en las notas del curso con RAG) rodeado de todo lo que hace falta para confiar en él en producción:

- **Observabilidad**: cada paso del grafo es un *span* (duración, tokens, costo, error). La consola los muestra **en vivo por SSE** y las trazas quedan guardadas.
- **Human-in-the-loop**: un reembolso nunca se ejecuta solo. El grafo (LangGraph) se pausa en un checkpoint, una persona aprueba o rechaza desde el panel y el agente retoma.
- **Seguridad**: API keys con *scopes*, cookie de sesión firmada (`httpOnly`, `SameSite=Strict`), CSRF de doble envío, bloqueo por fuerza bruta, cuotas por plan (token bucket), headers de seguridad.
- **Guardrails**: detección de prompt injection, canario en el system prompt que nunca debe salir, y enmascarado de PII **antes** del modelo y de la base.
- **Métricas**: `/metrics` en formato Prometheus (turnos, latencias, tokens, costo, esperas por aprobación humana).
- **Interfaz**: una consola React (chat, cascada de spans, panel de trazas y aprobaciones) servida por nginx en el mismo origen que la API.

Reutiliza lo construido antes: la cadena de proveedores con respaldo (P5), el RAG sobre `04-sistema-rag/notas` (P4/P6) y la API con Docker (P11).

### Cómo funciona

| Paso | Qué pasa |
|------|----------|
| 1 | El cliente se autentica (API key → cookie de sesión) y manda un mensaje a `POST /chat` o `/chat/stream` |
| 2 | Cuota por plan + guardrails de entrada (PII enmascarada, injection marcada) |
| 3 | El grafo corre: el modelo decide qué herramienta usar (catálogo, pedidos, RAG, reembolso) |
| 4 | Si pide un reembolso: `prepare_approval` → `wait_human` (pausa en el checkpoint) → `apply_decision` |
| 5 | Cada nodo emite un span al bus de eventos; SSE lo empuja a la consola y queda en SQLite |
| 6 | Guardrails de salida (canario, PII) y respuesta con `trace_id` y `message_id` |

### Endpoints

| Método | Ruta | Para qué | Scope |
|---|---|---|---|
| `GET` | `/health`, `/health/ready` | liveness / readiness (base) | — |
| `GET` | `/metrics` | Prometheus | — |
| `POST` | `/chat`, `/chat/stream` | un turno (JSON o SSE con spans en vivo) | `chat:write` |
| `GET` | `/traces`, `/traces/{id}`, `/traces/{id}/events` | trazas y su stream | `traces:read` |
| `GET` | `/approvals`, `/approvals/{id}` | cola de aprobaciones / estado de una | `approvals:write` / chat |
| `POST` | `/approvals/{id}/decision` | aprobar o rechazar y reanudar al agente | `approvals:write` |
| `POST` | `/feedback` | 👍 / 👎 sobre una respuesta | — |
| `GET` / `POST` / `DELETE` | `/session` | quién soy / entrar con API key / salir | — |
| `GET` / `POST` | `/clients`, `/maintenance/purge` | administración | `admin:all` |

Swagger en `/docs`. Detrás de nginx, la API cuelga de `/api`.

### Cómo ejecutarlo

Requisitos: Docker, o Python 3.12+ y Node 24 para correrlo sin contenedores; y al menos una API key de proveedor (ver `.env.example`).

#### Con Docker (recomendado)

```bash
cd 12-observability-and-security
cp .env.example .env          # completá JWT_SECRET y al menos un proveedor
docker compose up --build     # http://localhost:8080
```

La primera vez no hay ninguna API key. Se crea **dentro del contenedor** y se pega en la pantalla de entrada (se muestra una sola vez):

```bash
docker compose exec api python -m cli key --name "Ops" --scopes admin:all --plan pro
```

Si se crea con la CLI en la máquina, `DB_PATH` apunta a otra base que la del contenedor y la clave no sirve.

#### Sin Docker

```bash
cd 12-observability-and-security
python -m venv .venv && .venv\Scripts\activate      # o source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env     # y poné DB_PATH=./agent.db
python -m cli key --name "Ops" --scopes admin:all --plan pro

uvicorn api:app --reload                   # la API, en :8000
cd web && npm install && npm run dev       # la SPA, en :5173 (proxy a /api)
```

#### RAG sobre las notas

La herramienta `search_notes` responde sobre `04-sistema-rag/notas` (la misma base de conocimiento de los proyectos 4, 6 y 11), no sobre la web. Indexar usa embeddings de Gemini y tarda, así que arranca solo en un hilo: la **primera** consulta responde "estoy preparando el índice" y desde la segunda ya devuelve fragmentos con su fuente. La indexación es **reanudable** (guarda documento por documento; si se corta por una cuota 429 no tira lo ya pagado) y el índice vive en el volumen `/data`.

#### Probar

```bash
pytest -q               # 180 tests
ruff check .
cd web && npm test && npm run build
```

### Estructura

```
12-observability-and-security/
├── api.py             # FastAPI: chat, SSE, trazas, aprobaciones, feedback, sesión
├── agent.py           # el grafo de LangGraph: guardrails, nodos, tools, HITL
├── persistence.py     # SQLite: clientes, conversaciones, trazas, aprobaciones, auditoría
├── security.py        # API keys, JWT, cookies, CSRF, scopes, cuotas
├── traces.py          # Tracer, bus de eventos, correlación por request
├── metrics.py         # Prometheus
├── guardrails.py      # injection, canario    pii.py  # enmascarado de PII
├── model.py           # proveedores con cadena de respaldo (P5)
├── rag.py / notes.py  # índice Chroma + herramienta search_notes (P4/P6)
├── store.py           # catálogo y pedidos de demo
├── settings.py        # configuración validada al arrancar
├── cli.py             # key / status / purge
├── tests/             # 180 tests
├── web/               # consola React 19 + Vite + Tailwind
├── deploy/nginx.conf  # un solo origen: / es la SPA, /api es la API
├── Dockerfile         # 3 etapas (SPA, API, nginx); contexto: raíz del repo
└── docker-compose.yml
```

### Decisiones que conviene conocer

- **Un solo origen.** La cookie de sesión es `httpOnly` y `SameSite=Strict`; que la SPA y la API compartan host es lo que hace que sea seguro, no una comodidad.
- **Un worker.** SQLite y el checkpointer viven en el proceso. Escalar es ir a Postgres/Redis y a un bus de eventos, no subir `--workers`.
- **PII antes del modelo, no después.** La traza guarda lo enmascarado: la base no contiene lo que el usuario escribió.
- **SSE real.** El tipo del evento viaja dentro del JSON (`data: {"type": ...}`) para repartir con un solo `onmessage`.

### Errores típicos

| Síntoma | Causa | Solución |
|---|---|---|
| 403 en toda escritura desde el navegador | el nombre de la cookie CSRF no coincide entre cliente y API | un test de integración con el nombre del contrato |
| La SPA devuelve 404 en `/api/...` detrás de nginx | `proxy_pass http://api;` sin barra final deja el prefijo | `proxy_pass http://api/;` |
| Faltan headers de seguridad en `/assets/` | un `add_header` dentro de un `location` descarta los heredados | repetirlos en ese bloque |
| `docker compose build` falla con "no such file" | el contexto debe ser la raíz del repo (necesita `04-sistema-rag/notas`) | usar el `docker-compose.yml` de esta carpeta |
| Groq/OpenRouter fallan con `'dict' object has no attribute 'name'` | las tools llegaban como dicts canónicos | `model._to_openai_function` acepta ambas formas |
| La clave de la CLI no abre sesión | la CLI usó otra base que la API | crear la clave con `docker compose exec` |

### Habilidades cubiertas

- **Trazabilidad** de un agente: spans, costos, correlación por request y streaming en vivo.
- **Seguridad de una API con IA**: autenticación, scopes, CSRF, cuotas, PII, prompt injection y canarios.
- **Human-in-the-loop** con checkpoints persistentes de LangGraph.
- **Métricas** Prometheus y **despliegue** con Docker + nginx.

---

<a id="english"></a>
## 🇬🇧 English

### Overview

This is the jump from "an agent that works" to "an agent you can **operate**": an e-commerce agent (browses the catalog, looks up orders, issues refunds and searches the course notes with RAG) surrounded by everything needed to trust it in production:

- **Observability**: every graph step is a *span* (duration, tokens, cost, error). The console shows them **live over SSE** and traces are stored.
- **Human-in-the-loop**: a refund never runs on its own. The LangGraph graph pauses at a checkpoint, a person approves or rejects from the panel and the agent resumes.
- **Security**: scoped API keys, signed session cookie (`httpOnly`, `SameSite=Strict`), double-submit CSRF, brute-force lockout, per-plan quotas (token bucket), security headers.
- **Guardrails**: prompt-injection detection, a canary in the system prompt that must never leave, and PII masking **before** the model and the database.
- **Metrics**: `/metrics` in Prometheus format (turns, latencies, tokens, cost, time spent waiting for a human).
- **UI**: a React console (chat, span waterfall, trace and approval panel) served by nginx on the same origin as the API.

It reuses earlier work: the provider fallback chain (P5), RAG over `04-sistema-rag/notas` (P4/P6) and the Dockerized API (P11).

### How it works

| Step | What happens |
|------|--------------|
| 1 | The client authenticates (API key → session cookie) and sends a message to `POST /chat` or `/chat/stream` |
| 2 | Per-plan quota + input guardrails (PII masked, injection flagged) |
| 3 | The graph runs: the model picks a tool (catalog, orders, RAG, refund) |
| 4 | On a refund request: `prepare_approval` → `wait_human` (paused at the checkpoint) → `apply_decision` |
| 5 | Each node emits a span to the event bus; SSE pushes it to the console and it is stored in SQLite |
| 6 | Output guardrails (canary, PII) and the answer comes back with `trace_id` and `message_id` |

### Endpoints

See the Spanish table above (paths, methods and scopes are identical). Swagger at `/docs`; behind nginx the API lives under `/api`.

### Getting started

Requirements: Docker, or Python 3.12+ and Node 24 to run without containers; and at least one provider API key (see `.env.example`).

#### With Docker (recommended)

```bash
cd 12-observability-and-security
cp .env.example .env          # set JWT_SECRET and at least one provider
docker compose up --build     # http://localhost:8080
```

The first time there is no API key. Create it **inside the container** and paste it into the login screen (it is shown only once):

```bash
docker compose exec api python -m cli key --name "Ops" --scopes admin:all --plan pro
```

Creating it with the CLI on the host would point `DB_PATH` at a different database than the container's, and the key would not work.

#### Without Docker

```bash
cd 12-observability-and-security
python -m venv .venv && .venv\Scripts\activate      # or source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env     # and set DB_PATH=./agent.db
python -m cli key --name "Ops" --scopes admin:all --plan pro

uvicorn api:app --reload                   # the API, on :8000
cd web && npm install && npm run dev       # the SPA, on :5173 (proxy to /api)
```

#### RAG over the notes

The `search_notes` tool answers from `04-sistema-rag/notas` (the same knowledge base as projects 4, 6 and 11), not the web. Indexing uses Gemini embeddings and takes a while, so it starts by itself in a thread: the **first** query answers "preparing the index" and from the second one on it returns chunks with their source. Indexing is **resumable** (it saves document by document; a 429 quota error halfway does not throw away what was already paid for) and the index lives on the `/data` volume.

#### Testing

```bash
pytest -q               # 180 tests
ruff check .
cd web && npm test && npm run build
```

### Layout

See the Spanish tree above. Each module has a single responsibility (API, agent graph, persistence, security, tracing, metrics, guardrails, providers, RAG, store, settings, CLI, web console, nginx).

### Design decisions

- **Single origin.** The session cookie is `httpOnly` + `SameSite=Strict`; sharing the host between SPA and API is what makes that safe, not a convenience.
- **One worker.** SQLite and the checkpointer live in the process. Scaling means Postgres/Redis and an event bus, not `--workers`.
- **PII before the model, not after.** Stored traces carry the masked text: the database never contains what the user typed.
- **Real SSE.** The event type travels inside the JSON (`data: {"type": ...}`) so the client dispatches with a single `onmessage`.

### Common issues

See the Spanish table above: CSRF cookie name mismatch (403 on every write), nginx `proxy_pass` trailing slash (404 under `/api`), `add_header` inheritance inside `location`, Docker build context (must be the repo root), OpenAI-compatible tool format, and creating the first key inside the container.

### Skills covered

- **Tracing** an agent: spans, cost, per-request correlation and live streaming.
- **Securing an AI API**: authentication, scopes, CSRF, quotas, PII, prompt injection and canaries.
- **Human-in-the-loop** with persistent LangGraph checkpoints.
- **Prometheus metrics** and **deployment** with Docker + nginx.
