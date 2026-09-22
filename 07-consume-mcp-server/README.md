# Project 7 — Consume an existing MCP server (Postgres) / Proyecto 7 — Consumir un servidor MCP existente (Postgres)

**🌐 Language / Idioma:** [English](#english) · [Español](#español)

---

<a id="english"></a>
## 🇬🇧 English

### Overview

Until now every tool of the agent was written by us. This project breaks that: the agent connects to an **MCP server we did NOT write** — `@modelcontextprotocol/server-postgres`, from the community — and the tools that server exposes appear as **just another tool** of the LangGraph agent, exactly like first-class function calling.

The agent mixes both worlds:

- the **4 store tools** (search, stock, exact money calculations, coupons) from Project 5, and
- the **`query` tool** the MCP Postgres server exposes (READ-ONLY SQL against a real database).

As always, **the model decides** when to run SQL through MCP and when to use the local store tools. The framework's ReAct loop orchestrates everything — the MCP call is just another step in the graph.

The database is **self-contained**: the project ships a `docker-compose.yml` with a Postgres and an auto-loaded seed (tiny academic system: students, courses, subjects). No external database or credentials required. Of course, you can point `MCP_CONNECTION_STRING` at any other Postgres.

### How it works

Two programs, one standard:

```
your agent (LangGraph, sync)
   │  mcp_client.execute("query", sql=...)
   ▼
MCP client (mcp SDK, async) — one asyncio task on a background thread
   │  stdio: tools.call
   ▼
@modelcontextprotocol/server-postgres  ← subprocess launched with npx
   │  SELECT ...
   ▼
Postgres in Docker (localhost:5432/demo_libreria)
```

- **MCP server**: `@modelcontextprotocol/server-postgres` runs as a **subprocess** launched with `npx`, receiving the connection string as an argument. It knows nothing about our agent; it just waits for MCP requests over **stdin/stdout**.
- **MCP client** (`mcp_client.py`): with the official `mcp` SDK it connects over stdio, and at startup **lists the tools** the server exposes (`tools/list`), converting them to `FunctionDeclaration` (plain JSON schema, as usual). To run one it uses `session.call_tool(name, arguments)`.

The nice thing about the standard: the Postgres MCP server exposes **a single `query` tool** with an `sql` parameter and a *"Run a read-only SQL query"* description. That is the whole integration — we discover it and use it.

### The async → sync bridge (the interesting part)

The `mcp` SDK is 100% async, but LangGraph nodes are sync. The bridge:

- an **event loop on a background thread**, holding the MCP session open (no reconnect per tool);
- **ALL of the MCP lifecycle runs inside ONE asyncio task** (`_loop_main`): it opens the subprocess and session, serves requests in a `while True` over an `asyncio.Queue`, and does the teardown on close. This is key: the `stdio_client` context manager cannot be opened and closed from different tasks (anyio raises *"Attempted to exit cancel scope in a different task"*), and if the garbage collector closes the generator, the session dies ("Connection closed");
- the sync layer schedules each request with `_loop.call_soon_threadsafe(queue.put_nowait, ...)` — **not** `Queue.put_nowait` from another thread, which is not thread-safe — and waits on a `concurrent.futures.Future`.

So `execute()` stays a plain function the dispatch can call just like `search_product`.

### The self-contained demo database

```bash
docker compose up -d          # from this folder
```

- Spins up `postgres:16-alpine` on `localhost:5432`.
- `db/seed.sql` runs **automatically the first time** the volume is created (Postgres `/docker-entrypoint-initdb.d`), loading a tiny academic system: `Estudiante` (6 students), `Curso` (3 courses), `Materia` (4 subjects).
- Tables are **PascalCase on purpose** — a nice lesson: in Postgres, quoted identifiers (`"Estudiante"`) are case-sensitive, unquoted ones are folded to lowercase (`estudiante`). The agent prompt reinforces this so the model quotes table names correctly.
- Demo credentials are baked into the compose (visible on purpose); they match the default `MCP_CONNECTION_STRING` in `mcp_client.py`, so you don't need a `.env` for the database at all.

To regenerate the demo data: `docker compose down -v && docker compose up -d`.

### Getting started

From the repo root (after the initial setup in the root `README.md`):

```bash
cd 07-consume-mcp-server
docker compose up -d                             # Postgres + seed (one time)
venv\Scripts\python.exe mcp_client.py --tools    # see the tools the server exposes
venv\Scripts\python.exe mcp_agent.py             # the agent
```

Point it at any other Postgres by setting `MCP_CONNECTION_STRING` in your `.env`:

```ini
# .env — only if you want a database other than the demo one
MCP_CONNECTION_STRING=postgresql://user:pass@host:5432/database
```

### Example session

Real output against the project's demo database (provider chain `PROVIDERS=gemini,groq,openrouter`):

```
  [MCP] Server @modelcontextprotocol/server-postgres · db: localhost:5432/demo_libreria
  [MCP] Tools available: query
Providers (with fallback): gemini, groq, openrouter
Store + Postgres agent via MCP (LangGraph). What do you need?

You> How many students are there?

  [Agent node] Model decided: call tool(s): query
  [Tools node] Running query with {'sql': 'SELECT COUNT(*) FROM "Estudiante"'}
  [Tools node] Result: [ {"count": "6"} ]
  [Agent node] Model decided: reply in text
Agent> There are 6 students in the database.

You> List the courses.

  [Agent node] Model decided: call tool(s): query
  [Tools node] Running query with {'sql': 'SELECT "nombre", "nivel" FROM "Curso"'}
  [Agent node] Model decided: reply in text
Agent> The database has 3 courses, all "Ingenieria en Sistemas" (1st, 2nd and 3rd year).

You> How much for 2 laptops?

  [Agent node] Model decided: call tool(s): search_product, calc_total
  [Tools node] Running search_product with {'term': 'laptop'}
  [Tools node] Running calc_total with {'quantity': 2, 'product': 'laptop'}
  [Agent node] Model decided: reply in text
Agent> 2 laptops cost $1,330,997.58 total (VAT included).
```

Note how one question chains two tools (search, then calculate) entirely from the store, while the database questions go through the external `query` tool — the model picks.

### Try it

- `What tables exist?` → MCP branch: `query` + `information_schema`.
- `How many students?` / `List the courses.` → MCP branch: `query`.
- `How much for 2 laptops?` → store branch (`calc_total`), no DB involved.
- `And what was the total I asked about?` → **memory** (checkpointer).

### Files

```
07-consume-mcp-server/
├── docker-compose.yml     # self-contained Postgres (demo credentials)
├── db/seed.sql            # auto-loaded demo data (students, courses, subjects)
├── mcp_client.py          # MCP client: one async task + thread-safe queue, tool discovery
├── mcp_agent.py           # store agent + the external MCP 'query' tool
└── provider.py            # multi-provider adapter (gemini | groq | openrouter)
```

### Skills covered

- Connecting to a **community MCP server** (Postgres) via the official `mcp` SDK over stdio
- **Automatic tool discovery**: `tools/list` → `FunctionDeclaration`, no hand-written integration
- The **async → sync bridge**: one asyncio task + `call_soon_threadsafe` queue + `concurrent.futures.Future`
- A **self-contained demo database** (Docker Compose + auto seed) so the project runs without external credentials
- READ-ONLY safety: write tools of the server are filtered out (`BLOCKED`)

---

<a id="español"></a>
## 🇪🇸 Español

### Resumen

Hasta ahora todas las herramientas del agente eran programadas por nosotros. Este proyecto rompe eso: el agente se conecta a un **servidor MCP que NO escribimos** — `@modelcontextprotocol/server-postgres`, de la comunidad — y las tools que ese servidor expone **aparecen como herramientas más** del agente LangGraph, como si fueran function calling propio.

El agente mezcla los dos mundos:

- las **4 tools de la tienda** (buscar, stock, cuentas exactas, cupones) del Proyecto 5, y
- la **tool `query`** que expone el servidor MCP de Postgres (SQL de **solo lectura** contra una base real).

Como siempre, **el modelo es el que decide** cuándo correr SQL vía MCP y cuándo usar las tools locales. El loop ReAct del framework orquesta todo — la llamada MCP es un paso más del grafo.

La base es **autocontenida**: el proyecto trae un `docker-compose.yml` con un Postgres y un seed que se carga solo (mini sistema académico: estudiantes, cursos, materias). No hace falta ninguna base externa ni credenciales. Obvio, podés apuntar `MCP_CONNECTION_STRING` a cualquier otro Postgres.

### Cómo funciona

Dos programas, un estándar:

```
tu agente (LangGraph, síncrono)
   │  mcp_client.execute("query", sql=...)
   ▼
cliente MCP (SDK mcp, asíncrono) — una sola task asyncio en un thread de fondo
   │  stdio: tools.call
   ▼
@modelcontextprotocol/server-postgres  ← subproceso lanzado con npx
   │  SELECT ...
   ▼
Postgres en Docker (localhost:5432/demo_libreria)
```

- **Servidor MCP**: `@modelcontextprotocol/server-postgres` corre como **subproceso** lanzado con `npx`, recibiendo la connection string como argumento. No sabe nada de nuestro agente; solo espera pedidos MCP sobre **stdin/stdout**.
- **Cliente MCP** (`mcp_client.py`): con el SDK oficial `mcp` se conecta por stdio y en el arranque **lista las tools** que expone el servidor (`tools/list`), convirtiéndolas en `FunctionDeclaration` (esquema JSON puro, igual que siempre). Para ejecutar una usa `session.call_tool(nombre, argumentos)`.

Lo lindo del estándar: el servidor postgres MCP expone **una sola tool `query`** con un parámetro `sql` y la descripción *"Run a read-only SQL query"*. Eso es toda la integración — la descubrimos y la usamos.

### El puente async → sync (lo interesante)

El SDK `mcp` es 100% asíncrono, pero los nodos de LangGraph son síncronos. La solución:

- un **event loop en un hilo de fondo**, con la sesión MCP abierta (sin reconectar por tool);
- **TODO el ciclo de vida MCP vive en UNA sola task asyncio** (`_loop_main`): abre el subproceso y la sesión, atiende pedidos en un `while True` sobre una `asyncio.Queue` y hace el teardown al cerrar. Esto es clave: el context manager `stdio_client` no puede abrirse y cerrarse desde tasks distintas (anyio lanza *"Attempted to exit cancel scope in a different task"*), y si el GC cierra el generador, la sesión muere ("Connection closed");
- la capa síncrona agenda cada pedido con `_loop.call_soon_threadsafe(queue.put_nowait, ...)` — **no** `Queue.put_nowait` desde otra hebra, que no es thread-safe — y espera el resultado en un `concurrent.futures.Future`.

Así `execute()` sigue siendo una función normal que el dispatch usa igual que a `search_product`.

### La base de datos autocontenida

```bash
docker compose up -d          # desde esta carpeta
```

- Levanta `postgres:16-alpine` en `localhost:5432`.
- `db/seed.sql` se ejecuta **solo, la primera vez** que se crea el volumen (Postgres `/docker-entrypoint-initdb.d`), cargando un mini sistema académico: `Estudiante` (6 estudiantes), `Curso` (3 cursos), `Materia` (4 materias).
- Las tablas son **PascalCase a propósito** — una lección linda: en Postgres, los identificadores entre comillas (`"Estudiante"`) son case-sensitive; sin comillas pasan a minúsculas (`estudiante`). El prompt del agente refuerza esto para que el modelo ponga bien las comillas.
- Las credenciales de demo están en el compose (visibles a propósito); coinciden con el `MCP_CONNECTION_STRING` por defecto de `mcp_client.py`, así no necesitás `.env` para la base.

Para regenerar los datos de demo: `docker compose down -v && docker compose up -d`.

### Cómo ejecutarlo

Desde la raíz del repo (tras el setup inicial del `README.md` raíz):

```bash
cd 07-consume-mcp-server
docker compose up -d                             # Postgres + seed (una sola vez)
venv\Scripts\python.exe mcp_client.py --tools    # ver las tools que expone el servidor
venv\Scripts\python.exe mcp_agent.py             # el agente
```

Apuntalo a cualquier otro Postgres seteando `MCP_CONNECTION_STRING` en tu `.env`:

```ini
# .env — solo si querés una base distinta de la de demo
MCP_CONNECTION_STRING=postgresql://usuario:pass@host:5432/database
```

### Salida real

Salida real contra la base de demo del proyecto (cadena `PROVIDERS=gemini,groq,openrouter`):

```
  [MCP] Server @modelcontextprotocol/server-postgres · db: localhost:5432/demo_libreria
  [MCP] Tools available: query
Providers (with fallback): gemini, groq, openrouter
Store + Postgres agent via MCP (LangGraph). What do you need?

You> How many students are there?

  [Agent node] Model decided: call tool(s): query
  [Tools node] Running query with {'sql': 'SELECT COUNT(*) FROM "Estudiante"'}
  [Tools node] Result: [ {"count": "6"} ]
  [Agent node] Model decided: reply in text
Agent> There are 6 students in the database.

You> List the courses.

  [Agent node] Model decided: call tool(s): query
  [Tools node] Running query with {'sql': 'SELECT "nombre", "nivel" FROM "Curso"'}
  [Agent node] Model decided: reply in text
Agent> The database has 3 courses, all "Ingenieria en Sistemas" (1st, 2nd and 3rd year).

You> How much for 2 laptops?

  [Agent node] Model decided: call tool(s): search_product, calc_total
  [Tools node] Running search_product with {'term': 'laptop'}
  [Tools node] Running calc_total with {'quantity': 2, 'product': 'laptop'}
  [Agent node] Model decided: reply in text
Agent> 2 laptops cost $1,330,997.58 total (VAT included).
```

Notá cómo una pregunta encadena dos tools (buscar y calcular) solo de la tienda, mientras que las preguntas de la base van por la tool `query` externa — elige el modelo.

### Preguntas para probar

- `What tables exist?` → rama MCP: `query` + `information_schema`.
- `How many students?` / `List the courses.` → rama MCP: `query`.
- `How much for 2 laptops?` → rama tienda (`calc_total`), sin tocar la base.
- `And what was the total I asked about?` → **memoria** del checkpointer.

### Estructura

```
07-consume-mcp-server/
├── docker-compose.yml     # Postgres autocontenido (credenciales de demo)
├── db/seed.sql            # datos de demo que se cargan solos (estudiantes, cursos, materias)
├── mcp_client.py          # cliente MCP: una task async + cola thread-safe, descubrimiento de tools
├── mcp_agent.py           # agente de la tienda + la tool MCP externa 'query'
└── provider.py            # adaptador multi-proveedor (gemini | groq | openrouter)
```

### Habilidades cubiertas

- Conexión a un **servidor MCP de la comunidad** (Postgres) con el SDK oficial `mcp` por stdio
- **Descubrimiento automático de tools**: `tools/list` → `FunctionDeclaration`, sin integración escrita a mano
- El **puente async → sync**: una sola task asyncio + cola `call_soon_threadsafe` + `concurrent.futures.Future`
- **Base de datos autocontenida** (Docker Compose + seed automático) para que el proyecto corra sin credenciales externas
- Seguridad solo lectura: las tools de escritura del servidor se filtran con `BLOCKED`