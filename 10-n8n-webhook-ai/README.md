# Proyecto 10 — Automatización low-code con n8n (webhook → LLM → archivo) / Project 10 — Low-code automation with n8n (webhook → LLM → file)

**🌐 Idioma / Language:** [Español](#español) · [English](#english)

---

<a id="español"></a>
## 🇪🇸 Español

### Resumen

Los proyectos anteriores resolvieron todo con **código** (Python, LangGraph, MCP). Este proyecto cambia de palanca: **n8n**, una plataforma low-code donde el flujo se arma con **nodos visuales** conectados en un lienzo, y solo se escribe código donde conviene (nodo de código custom).

Caso de uso real: **resumidor/clasificador de consultas vía webhook**. Cualquier sistema le hace un `POST` con una consulta y n8n la clasifica (categoría + prioridad), la resume y **guarda un archivo Markdown** local:

| Nodo | Tipo | Qué hace |
|---|---|---|
| **Webhook** | trigger visual | Expone `POST /webhook/consulta`; dispara el flujo |
| **LLM** | HTTP Request visual | Llama a una API OpenAI-compatible (Groq por defecto) para clasificar + resumir en JSON |
| **Armar Markdown** | **código custom (JavaScript)** | Parsea la respuesta, valida y arma el `.md` |
| **Convertir a archivo** | visual | Pasa el texto a binario (con nombre de archivo) |
| **Escribir archivo** | visual | Guarda en `/data/salidas/<consulta-...>.md` |

Entregable: **flujo de n8n funcionando de punta a punta**, versionado como *workflow-as-code* (un `workflow.json` reimportable), con caso real y demo visible.

### Cómo funciona el flujo

1. **Webhook** — recibe `POST /webhook/consulta` con `{"consulta": "..."}` y responde `{"message":"Workflow was started"}` (modo `onReceived`).
2. **LLM** — `POST` a `$env.LLM_BASE_URL` con body armado por **expresión** `{{ }}`. La **API key NO está en el flujo**: el nodo usa una credencial tipo **Header Auth** (`Authorization: Bearer <key>`). En n8n 2.x el Webhook envuelve la consulta, por eso se lee `$json.consulta ?? $json.body?.consulta`.
3. **Armar Markdown** — nodo de código (JavaScript): parsea `choices[0].message.content` (JSON que a veces viene envuelto en ```` ```json ````), valida y arma el `.md`. Como el LLM **reemplaza el item**, la consulta original se recupera con `$('Webhook').first().json`.
4. **Convertir a archivo** → **Escribir archivo** — guardan el Markdown en `/data/salidas`. Ojo: Convert to File **vacía el json**, por eso el nombre se arma directo desde el nodo origin: `$('Armar Markdown').first().json.filenombre`.

### Cómo ejecutarlo

Requisitos: Docker + una API key de un proveedor OpenAI-compatible (ej. [Groq](https://console.groq.com/keys)).

```bash
# 1) Levantar n8n (los nodos de file-access y $env ya vienen configurados en el compose)
docker compose up -d

# 2) Primer inicio: creá el usuario owner en http://localhost:5678

# 3) Importar y publicar el workflow
#    - Settings > Import > workflow.json  (o n8n import:workflow)
#    - Creá la credencial Header Auth (Authorization: Bearer <TU_KEY>) y
#      seleccionala en el nodo LLM.
#    - Publicá el workflow (botón Publish; en n8n 2.x reemplazó al toggle Active).

# 4) Probar (PowerShell usa archivo para evitar romper el JSON en línea)
curl.exe -s -X POST http://localhost:5678/webhook/consulta `
  -H "Content-Type: application/json" `
  --data-binary "@consulta.json"
```

Configuración vía `.env` (ya viene `.env.example`; no se commitean keys):

```dotenv
LLM_BASE_URL=https://api.groq.com/openai/v1/chat/completions
LLM_MODEL=openai/gpt-oss-120b
LLM_SYSTEM_PROMPT=Eres un asistente experto en clasificar y resumir consultas de clientes.
```

Salida real (`salidas/consulta-2026-09-23T19-10-11-210Z.md`, también en `example-output.md`):

```markdown
# Clasificacion de consulta

- **Categoria:** Venta
- **Prioridad:** Alta
- **Fecha:** 2026-09-23T19:10:11.210Z

## Consulta original

> Quero cotizar 50 notebooks modelo ThinkPad para un cliente corporativo, cuando pueden entregarme el presupuesto?

## Resumen

El cliente desea cotizar 50 notebooks modelo ThinkPad para un cliente corporativo. Solicita recibir el presupuesto. Necesita información sobre precios y tiempos de entrega.
```

### Preguntas para probar

- ¿Cómo disparo el flujo? → `POST /webhook/consulta` (producción) o el botón "Listen for test event" + `/webhook-test/consulta` (te muestra la ejecución en el canvas).
- ¿El LLM usa la key de quién? → la de la credencial Header Auth de la UI. La key nunca está en el repo.
- ¿Qué pasa si llega sin `consulta`? → el LLM lo ve y el resumen avisa ("El cliente no proporcionó información").
- ¿Y si quiero guardar en Sheets/Slack/Gmail en vez de un archivo? → cambiar el nodo **Escribir archivo** por el de destino; el resto del flujo no cambia.

### Estructura

```
10-n8n-webhook-ai/
├── docker-compose.yml   # n8n con volúmenes, $env habilitado y file-access restringido
├── workflow.json        # el flujo versionado (webhook → LLM → markdown → archivo)
├── .env.example         # template: URL del LLM, modelo, prompt de sistema
└── example-output.md    # salida real de una ejecución
```

### Errores típicos

| Error / síntoma | Qué significa | Cómo resolverlo |
|---|---|---|
| `access to env vars denied` en `$env.XXX` | n8n 2.x bloquea `$env` por defecto | `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` en el compose y recrear el contenedor |
| `Access to the file is not allowed` | Los nodos de archivo solo tienen permitidos ciertos paths | `N8N_RESTRICT_FILE_ACCESS_TO=/data/salidas;...` en el compose |
| `EISDIR: ... open '/data/salidas'` | El nombre de archivo llegó vacío (Convert to File vacía el json) | Usar `$('Armar Markdown').first().json.filenombre` en el nodo Write, no `$json` |
| LLM responde 404 "resource could not be found" | El modelo de `LLM_MODEL` no existe en el proveedor | Cambiar a un modelo vigente (`openai/gpt-oss-120b` funciona en Groq) y recrear |
| `SSL Issue: consider using the 'Ignore SSL issues' option` | La red hace inspección SSL (MITM); el contenedor no confía en el cert | El `workflow.json` trae `options.allowUnauthorizedCerts: true` (seguro solo para redes que inspeccionan SSL o sandboxes locales). Si tu red no lo hace, borrá esa opción |
| Reimportar desactiva el webhook (`Cannot POST ... not registered`) | `import:workflow` deja el workflow en draft | Volver a **Publish** (UI) o `n8n publish:workflow --id=<id>` + `docker compose restart` |

### Habilidades cubiertas

- **Low-code real**: flujo de punta a punta con nodos visuales + un nodo de código custom
- **Workflow-as-code**: flujo versionable y reimportable (`workflow.json`)
- **Expresiones `{{ }}` y funciones puras de nodos**: `$json`, `$env`, `$('Nodo').first().json`
- **Secretos fuera del flujo**: credencial Header Auth en la UI, keys solo en `.env` local
- **Seguridad del contenedor**: `N8N_BLOCK_ENV_ACCESS_IN_NODE`, `N8N_RESTRICT_FILE_ACCESS_TO`
- **Depuración real**: de sándbox de env/archivos, modelos descontinuados e inspección TLS de la red a un flujo estable

---

<a id="english"></a>
## 🇬🇧 English

### Overview

The previous projects solved everything with **code** (Python, LangGraph, MCP). This one switches gears: **n8n**, a low-code platform where the flow is built with **visual nodes** wired together on a canvas, and code is only written where it pays off (a custom code node).

Real-world use case: **webhook-driven query summarizer/classifier**. Any system `POST`s a query and n8n classifies it (category + priority), summarizes it and **writes a local Markdown file**:

| Node | Type | What it does |
|---|---|---|
| **Webhook** | visual trigger | Exposes `POST /webhook/consulta`; starts the flow |
| **LLM** | visual HTTP Request | Calls an OpenAI-compatible API (Groq by default) to classify + summarize as JSON |
| **Armar Markdown** | **custom code (JavaScript)** | Parses the answer, validates and builds the `.md` |
| **Convert to file** | visual | Turns the text into binary (with a filename) |
| **Write file** | visual | Saves to `/data/salidas/<consulta-...>.md` |

Deliverable: **an end-to-end n8n flow**, versioned as *workflow-as-code* (a re-importable `workflow.json`), with a real use case and a visible demo.

### Getting started

Requirements: Docker + an API key from an OpenAI-compatible provider (e.g. [Groq](https://console.groq.com/keys)).

```bash
# 1) Start n8n ($env and file-access are already configured in the compose file)
docker compose up -d

# 2) First start: create the owner user at http://localhost:5678

# 3) Import and publish
#    - Settings > Import > workflow.json   (or n8n import:workflow)
#    - Create a Header Auth credential (Authorization: Bearer <YOUR_KEY>) and
#      select it on the LLM node.
#    - Publish the workflow (Publish button; in n8n 2.x it replaced the Active toggle).

# 4) Try it
curl -s -X POST http://localhost:5678/webhook/consulta \
  -H "Content-Type: application/json" \
  --data-binary "@consulta.json"
```

Configuration via `.env` (`.env.example` included; no keys are committed):

```dotenv
LLM_BASE_URL=https://api.groq.com/openai/v1/chat/completions
LLM_MODEL=openai/gpt-oss-120b
LLM_SYSTEM_PROMPT=Eres un asistente experto en clasificar y resumir consultas de clientes.
```

Real output (`salidas/consulta-2026-09-23T19-10-11-210Z.md`, also in `example-output.md`):

```markdown
# Clasificacion de consulta

- **Categoria:** Venta
- **Prioridad:** Alta
- **Fecha:** 2026-09-23T19:10:11.210Z

## Consulta original

> Quero cotizar 50 notebooks modelo ThinkPad para un cliente corporativo, cuando pueden entregarme el presupuesto?

## Resumen

El cliente desea cotizar 50 notebooks modelo ThinkPad para un cliente corporativo. Solicita recibir el presupuesto. Necesita información sobre precios y tiempos de entrega.
```

### Files

```
10-n8n-webhook-ai/
├── docker-compose.yml   # n8n with volumes, $env enabled and restricted file access
├── workflow.json        # the versioned flow (webhook → LLM → markdown → file)
├── .env.example         # template: LLM URL, model, system prompt
└── example-output.md    # real output from a run
```

### Typical errors

| Error / symptom | What it means | How to fix it |
|---|---|---|
| `access to env vars denied` in `$env.XXX` | n8n 2.x blocks `$env` by default | `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` in the compose file, recreate the container |
| `Access to the file is not allowed` | File nodes are only allowed certain paths | `N8N_RESTRICT_FILE_ACCESS_TO=/data/salidas;...` in the compose file |
| `EISDIR: ... open '/data/salidas'` | The filename arrived empty (Convert to File empties the json) | Use `$('Armar Markdown').first().json.filenombre` on the Write node, not `$json` |
| LLM answers 404 "resource could not be found" | The `LLM_MODEL` does not exist at the provider | Switch to a live model (`openai/gpt-oss-120b` works on Groq) and recreate |
| `SSL Issue: consider using the 'Ignore SSL issues' option` | The network does SSL inspection (MITM); the container does not trust the cert | `workflow.json` ships with `options.allowUnauthorizedCerts: true` (safe only for SSL-inspecting networks or local sandboxes). If your network does not inspect SSL, remove that option |
| Re-import deactivates the webhook (`Cannot POST ... not registered`) | `import:workflow` leaves the workflow as draft | Publish again (UI) or `n8n publish:workflow --id=<id>` + `docker compose restart` |

### Skills covered

- **Real low-code**: an end-to-end flow with visual nodes + one custom code node
- **Workflow-as-code**: versionable, re-importable flow (`workflow.json`)
- **`{{ }}` expressions and pure node functions**: `$json`, `$env`, `$('Node').first().json`
- **Secrets kept out of the flow**: Header Auth credential in the UI, keys only in a local `.env`
- **Container security**: `N8N_BLOCK_ENV_ACCESS_IN_NODE`, `N8N_RESTRICT_FILE_ACCESS_TO`
- **Real debugging**: from env/file sandboxes, discontinued models and network TLS inspection to a stable flow