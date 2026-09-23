# Proyecto 8 — Servidor MCP propio / Project 8 — Your own MCP server

**🌐 Idioma / Language:** [Español](#español) · [English](#english)

---

<a id="español"></a>
## 🇪🇸 Español

### Resumen

El Proyecto 7 **consumió** un servidor MCP ajeno (`@modelcontextprotocol/server-postgres`) y sus tools aparecieron como herramientas del agente sin escribir la integración. Este proyecto **da vuelta el rol**: construimos **nuestro propio servidor MCP**, con el SDK oficial de Python (`mcp` 2.x, clase `MCPServer`), que expone por el estándar herramientas que **ya habíamos programado**:

- las **4 tools de la tienda** del Proyecto 5 (`search_product`, `check_stock`, `calc_total`, `apply_coupon`), reutilizadas tal cual;
- la **búsqueda en la base de conocimiento** (`search_notes`, el RAG del Proyecto 4/6) via el módulo compartido `rag.py`.

Cualquier cliente compatible (Claude Desktop, otro agente con LangGraph, un script con el SDK `mcp`, n8n…) se conecta por **stdio**, lista las tools con `tools/list` y las ejecuta con `tools/call`. No hay que reescribir la integración en cada cliente: todos hablan el mismo protocolo — **esa es la promesa de MCP**.

Entregable: **servidor MCP propio, corriendo localmente, consumible desde cualquier cliente compatible.**

### Cómo funciona: un decorador sobre funciones que ya teníamos

Lo más interesante del proyecto es lo poco que hay que escribir:

```python
from mcp.server.mcpserver import MCPServer

SERVER = MCPServer(name="agentes-ia-projects", version="0.1.0")

@SERVER.tool()
def search_product(term: str) -> str:
    """Search the store catalog by partial text in the name."""
    return p5_search(term)  # la implementación ya existía del P5
```

- `MCPServer` (mcp **2.x**) es el sucesor de `FastMCP` (que en 1.x se importaba de `mcp.server.fastmcp`). Es el mismo concepto, renombrado en el SDK v2.
- El decorador `@SERVER.tool()` extrae del `docstring` la **descripción** que verá el cliente y de la firma (`term: str`) el **esquema JSON** de parámetros — exactamente como las `FunctionDeclaration` que armábamos a mano en proyectos anteriores.
- `asyncio.run(SERVER.run_stdio_async())` levanta el transporte stdio y atiende los pedidos de cualquier cliente.

Un detalle que cuida el SDK 2.x: mientras atiende por stdio, **desvía fd 1 (stdout) a stderr**, para que ningún `print` del código ni de las librerías (p. ej. los logs de ChromaDB) corrompa el protocolo.

### El self-check: `--selftest`

Para probar el server sin levantar un cliente externo, `mcp_server.py --selftest` actúa como cliente de **sí mismo**: lo lanza como subproceso con `StdioServerParameters(command=sys.executable, args=[ruta])`, se conecta con el mismo SDK `mcp` (lado cliente, como en el P7) y ejecuta una tool de la tienda y una de RAG.

```bash
venv\Scripts\python.exe 08-custom-mcp-server\mcp_server.py --selftest
```

Salida real (respuesta RAG acortada):

```
Servidor MCP propio levantado. Herramientas disponibles (5):
  - search_product: Search the store catalog by partial text in the name or list the full catalog.
  - check_stock: Return the available stock of an exact catalog product.
  - calc_total: Calculate subtotal, VAT (21%) and total for buying a quantity of a product.
  - apply_coupon: Validate a discount coupon code and return its discount percentage.
  - search_notes: Search the AI Agents course notes and return the most relevant chunks (RAG).

> search_product({"term": "monitor"})
Productos encontrados:
- monitor: $249,999.00 (stock: 8)

> calc_total({"product": "monitor", "quantity": 2})
Subtotal: $499,998.00
IVA (21%): $104,999.58
Total: $604,997.58

> search_notes({"query": "What is MCP?"})
[05-mcp.md] (distancia 0.272)
# MCP: Model Context Protocol
**MCP (Model Context Protocol)** is an open standard, originated by Anthropic...
```

Fijate que el último ejemplo **genera embeddings reales de Gemini** (la consulta se convierte en vector con `GEMINI_API_KEY` del `.env`) y recupera de ChromaDB el fragmento correcto de la base de conocimiento del repo — el mismo RAG del P4/6, ahora sirviendo por un canal MCP estándar. La tool `search_notes` reutiliza el índice compartido de `04-sistema-rag/` (misma colección `notas_agentes`), así que no duplica nada.

### Para conectar un cliente externo (cualquiera)

Corré el servidor en un terminal:

```bash
venv\Scripts\python.exe 08-custom-mcp-server\mcp_server.py
```

Desde cualquier cliente compatible alcanza con apuntar el comando a este archivo. Por ejemplo, en una config `mcp.json`:

```json
{
  "mcpServers": {
    "proyecto8": {
      "command": "C:\\...\\agentes-ia\\venv\\Scripts\\python.exe",
      "args": ["C:\\...\\agentes-ia\\08-custom-mcp-server\\mcp_server.py"]
    }
  }
}
```

El cliente verá las 5 tools como propias — igual que en el P7 veíamos `query` del servidor externo, pero ahora **el servidor lo programamos nosotros**.

### Qué pudo haber sido pero no fue (a propósito)

Un servidor MCP puede exponer también **Resources** (datos que el cliente lee, como archivos) y **Prompts** (plantillas reutilizables). Acá solo usamos **tools** porque era el camino directo: son las funciones que ya sabíamos declarar. Resources y Prompts quedan como ejercicio natural para estirar este mismo server.

### Cómo ejecutarlo

Requisito: la API key de Gemini en el `.env` raíz (para el RAG). Las tools de la tienda no necesitan nada.

```bash
venv\Scripts\python.exe 08-custom-mcp-server\mcp_server.py --selftest    # prueba desde un cliente (recomendado)
venv\Scripts\python.exe 08-custom-mcp-server\mcp_server.py               # corre como servidor (stdio)
```

### Estructura

```
08-custom-mcp-server/
├── mcp_server.py     # nuestro servidor MCP: MCPServer + 4 tools de tienda (P5) + search_notes (RAG)
├── rag.py            # módulo RAG compartido (misma base de conocimiento que P4/P6)
└── README.md
```

### Habilidades cubiertas

- **Construir un servidor MCP propio** con el SDK oficial (`MCPServer`, mcp 2.x) sobre transporte stdio
- Exponer **tools ya programadas** (tienda P5 + RAG P4/6) con un simple decorador `@SERVER.tool()`
- Autogeneración del **esquema JSON** de cada tool desde la firma y el docstring
- **Self-check**: `--selftest` conecta el server consigo mismo como si fuera un cliente externo
- Reutilización del **índice RAG compartido** del repo (sin duplicar embeddings)
- Conciencia del trasfondo: por qué MCP vale la pena (estándar único vs. integraciones custom por cliente)

---

<a id="english"></a>
## 🇬🇧 English

### Overview

Project 7 **consumed** an existing MCP server (`@modelcontextprotocol/server-postgres`) and its tools showed up as agent tools without writing the integration. This project **flips the roles**: we build **our own MCP server**, with the official Python SDK (`mcp` 2.x, `MCPServer` class), exposing over the standard tools we **had already written**:

- the **4 store tools** from Project 5 (`search_product`, `check_stock`, `calc_total`, `apply_coupon`), reused as-is;
- the **knowledge-base search** (`search_notes`, the Project 4/6 RAG) through the shared `rag.py` module.

Any compatible client (Claude Desktop, another LangGraph agent, a script using the `mcp` SDK, n8n…) connects over **stdio**, lists the tools with `tools/list` and runs them with `tools/call`. No integration rewrite per client: everyone speaks the same protocol — **that is MCP's promise**.

Deliverable: **an MCP server of your own, running locally, consumable by any compatible client.**

### How it works: a decorator over functions we already had

The interesting part is how little you need to write:

```python
from mcp.server.mcpserver import MCPServer

SERVER = MCPServer(name="agentes-ia-projects", version="0.1.0")

@SERVER.tool()
def search_product(term: str) -> str:
    """Search the store catalog by partial text in the name."""
    return p5_search(term)  # the implementation already existed from P5
```

- `MCPServer` (mcp **2.x**) is the successor of `FastMCP` (which in 1.x came from `mcp.server.fastmcp`). Same concept, renamed in the SDK v2.
- The `@SERVER.tool()` decorator extracts the **description** the client sees from the docstring and the **JSON schema** of the parameters from the signature (`term: str`) — exactly like the `FunctionDeclaration`s we hand-built in earlier projects.
- `asyncio.run(SERVER.run_stdio_async())` starts the stdio transport and serves any client.

A neat detail the 2.x SDK handles for you: while serving over stdio it **diverts fd 1 (stdout) to stderr**, so no `print` from our code or third-party libraries (e.g. ChromaDB logs) corrupts the protocol.

### The self-check: `--selftest`

To test the server without a separate external client, `mcp_server.py --selftest` acts as a client of **itself**: it spawns this same file as a subprocess with `StdioServerParameters(command=sys.executable, args=[path])`, connects with the very same `mcp` SDK (client side, as in Project 7) and runs a store tool and a RAG tool.

```bash
venv\Scripts\python.exe 08-custom-mcp-server\mcp_server.py --selftest
```

Real output (RAG answer truncated):

```
Servidor MCP propio levantado. Herramientas disponibles (5):
  - search_product: Search the store catalog by partial text in the name or list the full catalog.
  ...
> search_notes({"query": "What is MCP?"})
[05-mcp.md] (distancia 0.272)
# MCP: Model Context Protocol
...
```

Note the console output is in Spanish (the repo targets Spanish-speaking audiences; your own usage can be in any language). The RAG example **creates real Gemini embeddings** (from `GEMINI_API_KEY` in `.env`) and retrieves the right chunk of the repo's knowledge base — the same P4/6 RAG, now served over a standard MCP channel. `search_notes` reuses the shared index in `04-sistema-rag/` (same `notas_agentes` collection), so nothing is embedded twice.

### Connecting an external client (any)

Run the server in a terminal:

```bash
venv\Scripts\python.exe 08-custom-mcp-server\mcp_server.py
```

From any compatible client you only point the command at this file. For example, in an `mcp.json` config:

```json
{
  "mcpServers": {
    "proyecto8": {
      "command": "C:\\...\\agentes-ia\\venv\\Scripts\\python.exe",
      "args": ["C:\\...\\agentes-ia\\08-custom-mcp-server\\mcp_server.py"]
    }
  }
}
```

The client sees the 5 tools as its own — just like Project 7 saw the external `query` tool, except now **we wrote the server**.

### What we deliberately left out

An MCP server can also expose **Resources** (data a client reads, e.g. files) and **Prompts** (reusable templates). Here we only use **tools** because it was the direct path: they are the functions we already knew how to declare. Resources and Prompts are a natural exercise to extend this same server.

### Getting started

Requirement: the Gemini API key in the root `.env` (for the RAG tool). The store tools need nothing.

```bash
venv\Scripts\python.exe 08-custom-mcp-server\mcp_server.py --selftest    # test from a client (recommended)
venv\Scripts\python.exe 08-custom-mcp-server\mcp_server.py               # runs as the server (stdio)
```

### Files

```
08-custom-mcp-server/
├── mcp_server.py     # our MCP server: MCPServer + 4 store tools (P5) + search_notes (RAG)
├── rag.py            # shared RAG module (same knowledge base as P4/P6)
└── README.md
```

### Skills covered

- **Building your own MCP server** with the official SDK (`MCPServer`, mcp 2.x) over stdio transport
- Exposing **existing tools** (store P5 + RAG P4/6) with a single `@SERVER.tool()` decorator
- Auto-generated **JSON schema** per tool from the signature and docstring
- **Self-check**: `--selftest` connects the server to itself as if it were an external client
- Reusing the repo's **shared RAG index** (no duplicated embeddings)
- Understanding **why MCP matters** (one standard vs. custom integrations per client)