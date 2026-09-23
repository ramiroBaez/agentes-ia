# Proyecto 9 — Orquestación multi-agente (supervisor + workers) / Project 9 — Multi-agent orchestration (supervisor + workers)

**🌐 Idioma / Language:** [Español](#español) · [English](#english)

---

<a id="español"></a>
## 🇪🇸 Español

### Resumen

Hasta el Proyecto 8 todo describía **un agente** (con herramientas, framework, MCP) resolviendo tareas solo. Este proyecto arma el patrón **manager-workers**: un **supervisor** que coordina y **3 workers especializados** que colaboran, corriendo sobre LangGraph.

Caso de uso: **informe de ventas**. El usuario pide algo tipo *"armá un informe de las ventas del primer trimestre"* y el equipo produce el texto final:

| Agente | Su trabajo | Tools |
|---|---|---|
| **Supervisor** | Recibe el pedido, delega de a un paso, decide cuándo cierra | tool sintética `delegate(worker, message)` |
| **worker data** | Traduce el pedido a consultas y devuelve los **datos crudos** | `query_sales`, `sales_schema` |
| **worker analyst** | Interpreta los datos y saca **conclusiones** (totales, top, tendencias) | `query_sales` |
| **worker writer** | Arma el **informe final** en Markdown | ninguna |

El supervisor **no hace el trabajo**: coordina. Es la división de roles del patrón manager-workers (un vendedor, una contadora y una programadora en vez de una sola persona que hace todo mal).

Entregable: **sistema multi-agente funcional para una tarea de varios pasos, con logs claros de qué hizo cada agente.**

### Lo nuevo vs Proyecto 5/6 (un solo agente con LangGraph)

| Concepto | Proyecto 5/6 | Proyecto 9 |
|---|---|---|
| Agentes | 1 (el agente de la tienda/tienda+notas) | **4** (supervisor + 3 workers) |
| System prompts | 1 para todo | **1 por agente**, acotado a su rol |
| Tools | todas juntas en un solo loop | **cada worker con sus propias tools** |
| Quién decide el flujo | el modelo (tool o texto) | el supervisor **delega** a un worker y rutea |
| Estado | historial de mensajes | historial + **resultados y decisiones acumulados** |
| Riesgo | loop en un turno | loop **entre agentes** (más grave) → triple control |

### Cómo funciona: supervisor que rutea + workers que trabajan

#### 1. El supervisor es un nodo más del grafo

El supervisor llama al modelo con **una sola tool sintética**: `delegate(worker, message)`. LangGraph mira qué worker eligió (arista condicional) y manda la ejecución a ese nodo:

```python
@graph: supervisor -> (conditional) -> worker_{worker} -> supervisor -> ...
```

El estado guarda **decisiones** (qué delegó) y **resultados** (qué devolvió cada worker), así el supervisor ve todo el progreso antes de decidir el siguiente paso.

#### 2. Cada worker corre su propio loop ReAct encapsulado

A diferencia de P5/P6 (un solo loop), aquí cada worker es un nodo que ejecuta su **propia mini-conversación** con su system prompt y sus tools, hasta responder en texto:

```python
def run_worker(role, system, funcs, tools, task):
    messages = [provider.user_msg(task)]
    for step in range(1, MAX_WORKER_STEPS + 1):
        turn = provider.chat(messages, system=system, funcs=funcs)
        if not turn["calls"]:
            return turn["text"]          # answered in text -> done
        ...  # execute tools and keep going
```

El worker devuelve solo el **resultado final** al estado compartido — no contamina la conversación con su interno.

#### 3. Triple control anti loop infinito

1. **`recursion_limit`** del `invoke` — tope global del grafo.
2. **Tope de delegaciones** (`MAX_DELEGATIONS = 6`) — el supervisor ve el contador en el estado y su prompt le ordena cerrar con lo que tenga; además hay regla **antiloop**: si un worker devolvió error, no lo reintentas, adaptás la estrategia o cerrás. Y la regla **WRITER**: el informe del writer se **acepta tal cual** — no se le pide "completar" ni "mejorar" (eso hacía que el supervisor sobre-delegara regenerando informe tras informe); el primer `writer` basta y se cierra con `finalize`.
3. **Tope interno por worker** (`MAX_WORKER_STEPS = 4`) — cada worker corta su propio loop.

Y de bonus: el adaptador `provider.py` provee **respaldo multi-proveedor** (gemini → groq → openrouter) **con backoff anti-cuota** (ver abajo). Si todos fallan a la vez, el worker lo reporta al supervisor (que decide con lo que tenga) en vez de tumbar el grafo.

#### Backoff anti-cuota en `provider.py` (compartido)

`provider.chat()` reintenta la cadena de proveedores ante fallos **transitorios** (429 quota/rate-limit, 503, "retry in Xs", el 400 intermitente de Groq `failed to render... harmony`): si TODOS fallaron con errores transitorios, espera **5s → 15s → 40s** y reintenta hasta 3 rondas. Los errores **permanentes** (API key inválida, schema mal) cortan enseguida, sin esperas. Esto evita que un pico de cuota tumbe una ejecución larga (un worker a mitad de su loop, o un supervisor que decidía el siguiente paso) — que era exactamente el síntoma de "no funcionó: todos los proveedores fallaron a la vez" sin informe.

### Logs claros: qué hizo cada agente

Cada agente imprime con su etiqueta, así se "ve pensar" al equipo:

```
  [Supervisor] Delegaciones usadas: 0/6
  [Supervisor] Decide → delegar a 'data': Necesito los datos de ventas del Q1...
  [Ruteador] state['decisions'][-1] = data → worker_data
    [Worker data] Recibe orden del supervisor: ...
      [data·paso 1] Turno del modelo (proveedor: groq) → herramienta(s): query_sales
      [data] ejecuté query_sales({'segment': 'Enero'}) → Registros: 9 | ...
  [Supervisor] Decide → delegar a 'analyst': Con el siguiente JSON de ventas del Q1...
    [Worker analyst] ...
  [Supervisor] Decide → delegar a 'writer': ...
  [Supervisor] Decide → delegar a 'finalize': Informe de ventas Q1 completado.
```

### Dataset embebido (amplio, para muchas preguntas)

`SALES` es una base simulada de **6 meses (ene→jun) × 8 productos en 4 categorías** = 54 registros (mes, categoría, producto, unidades, importe). Tiene **tendencia de verdad**: Q1 plano a la baja en periféricos, Q2 con picos en monitores y notebooks; audio estable. Eso permite preguntas de totales, comparativas, top productos, tendencias y anomalías.

Ejemplos reales de preguntas:
- *"Armá un informe de las ventas del primer trimestre (Q1): totales por mes y por categoría, el producto estrella y cómo evolucionó la tendencia."*
- *"Compará los dos trimestres (Q1 vs Q2). ¿Qué categoría creció más? ¿Cuál fue el mejor mes del semestre y el producto más vendido en unidades?"*
- *"¿Cómo evolucionaron los periféricos mes a mes? ¿Hay alguna caída preocupante?"*
- *"Dame el mejor y el peor mes del semestre, con sus totales."*

### Cómo ejecutarlo

Requisito: al menos una API key en el `.env` raíz (Gemini, Groq u OpenRouter — la cadena de respaldo usa lo que esté configurado).

```bash
venv\Scripts\python.exe 09-multi-agent-orchestration\multi_agent.py --info    # qué contiene el dataset
venv\Scripts\python.exe 09-multi-agent-orchestration\multi_agent.py --test    # demo automática: 2 pedidos de punta a punta
venv\Scripts\python.exe 09-multi-agent-orchestration\multi_agent.py           # modo chat interactivo
```

### Preguntas para probar

- ¿Cuántos agentes hay y qué hace cada uno? → los prints `[Supervisor]`, `[Worker data]`, etc.
- ¿El supervisor delega de a un paso o hace todo junto? → los prints: primero `data`, después `analyst`, después `writer`, después `finalize`.
- ¿Cada worker tiene tools propias? → el writer NO consulta la base (no tiene `query_sales`); data y analyst sí.
- ¿Qué pasa si le pedís algo que necesita Q1 y Q2? → el data consulta por separado y el analyst compara.
- ¿Hay límite de iteraciones? → contador `[Supervisor] Delegaciones usadas: N/6`; el grafo corta con lo que tenga.
- ¿Qué pasa si todos los proveedores fallan? → el worker reporta el error al supervisor, que cierra con lo que haya (no revienta el grafo).

### Estructura

```
09-multi-agent-orchestration/
├── multi_agent.py   # el sistema multi-agente: supervisor + 3 workers sobre LangGraph
├── provider.py      # adaptador multi-proveedor con backoff anti-cuota (compartido)
└── README.md
```

### Errores típicos

| Error / síntoma | Qué significa | Cómo resolverlo |
|---|---|---|
| `RuntimeError: All providers failed (...)` dentro de un worker | Cuota/red de TODOS los proveedores a la vez | Ya hay backoff automático en `provider.chat` (reintenta 3× con 5s/15s/40s si el fallo es transitorio). Si aún así sigue, revisá el `.env`: mové a `PROVIDERS` el proveedor que esté con cuota hoy (ej. `PROVIDERS=groq,openrouter,gemini`) |
| El supervisor repite la misma delegación | El modelo no ve avance en el estado (o ignoró la regla antiloop) | Fijate que los `results` acumulados estén en el `_context`; subí la regla en `SUPERVISOR_PROMPT` |
| `UnicodeEncodeError: 'charmap'` en consola | Windows cp1252 no entiende `→`/emoji | Ya resuelto con `sys.stdout.reconfigure(encoding="utf-8")` en `main()` |
| `GraphRecursionError` | Se alcanzó `recursion_limit` | Ajustá `RECURSION_LIMIT` (o bajá `MAX_DELEGATIONS`) |
| El informe "pierde" un mes | El contexto del supervisor recorta resultados largos a 4000 chars | Consultá el segmento por separado o subí el tope en `_context()` |

### Habilidades cubiertas

- **Patrón manager-workers** sobre LangGraph: un supervisor que coordina y workers que trabajan
- **Delegación jerárquica**: una tool sintética `delegate(worker, message)` como única salida del supervisor
- **Workers con tools propias y prompts acotados**, cada uno con su loop ReAct encapsulado
- **Triple control anti loop**: `recursion_limit` global + tope de delegaciones + tope interno por worker
- **Respaldo multi-proveedor con backoff anti-cuota** en el adaptador compartido
- **Arquitectura**: estado compartido acumulado (decisiones + resultados) para que el supervisor "vea" todo el progreso

---

<a id="english"></a>
## 🇬🇧 English

### Overview

Up to Project 8 everything described **a single agent** (with tools, framework, MCP) solving tasks alone. This project builds the **manager-workers** pattern: a **supervisor** that coordinates and **3 specialized workers** that collaborate, running on LangGraph.

Use case: **sales report**. The user asks something like *"build a sales report for the first quarter"* and the team produces the final text:

| Agent | Its job | Tools |
|---|---|---|
| **Supervisor** | Receives the request, delegates one step at a time, decides when to close | synthetic tool `delegate(worker, message)` |
| **data worker** | Translates the request into queries and returns **raw data** | `query_sales`, `sales_schema` |
| **analyst worker** | Interprets the data and draws **conclusions** (totals, top, trends) | `query_sales` |
| **writer worker** | Assembles the **final report** in Markdown | none |

The supervisor **does not do the work**: it coordinates. That is the manager-workers division of roles (a salesperson, an accountant and a writer instead of one person doing everything badly).

Deliverable: **a working multi-agent system for a multi-step task, with clear logs of what each agent did.**

### What's new vs Projects 5/6 (a single LangGraph agent)

| Concept | Projects 5/6 | Project 9 |
|---|---|---|
| Agents | 1 (the store / store+notes agent) | **4** (supervisor + 3 workers) |
| System prompts | 1 for everything | **1 per agent**, scoped to its role |
| Tools | all together in one loop | **each worker with its own tools** |
| Who decides the flow | the model (tool or text) | the supervisor **delegates** to a worker and routes |
| State | message history | history + **accumulated results and decisions** |
| Risk | loop in one turn | loop **between agents** (worse) → triple control |

### How it works: a routing supervisor + working workers

#### 1. The supervisor is one more graph node

The supervisor calls the model with **a single synthetic tool**: `delegate(worker, message)`. LangGraph looks at which worker it chose (conditional edge) and sends execution to that node:

```python
@graph: supervisor -> (conditional) -> worker_{worker} -> supervisor -> ...
```

The state stores **decisions** (what it delegated) and **results** (what each worker returned), so the supervisor sees all progress before deciding the next step.

#### 2. Each worker runs its own encapsulated ReAct loop

Unlike P5/P6 (one loop), here each worker is a node that runs its **own mini-conversation** with its own system prompt and tools, until it answers in text:

```python
def run_worker(role, system, funcs, tools, task):
    messages = [provider.user_msg(task)]
    for step in range(1, MAX_WORKER_STEPS + 1):
        turn = provider.chat(messages, system=system, funcs=funcs)
        if not turn["calls"]:
            return turn["text"]          # answered in text -> done
        ...  # execute tools and keep going
```

The worker returns only the **final result** to the shared state — it does not pollute the conversation with its internals.

#### 3. Triple anti-infinite-loop control

1. **`recursion_limit`** of `invoke` — global graph cap.
2. **Delegation cap** (`MAX_DELEGATIONS = 6`) — the supervisor sees the counter in the state and its prompt orders it to close with what it has; plus an **anti-loop** rule: if a worker returned an error, do not retry it, adapt the strategy or close. And the **WRITER rule**: the writer's report is **accepted as-is** — never ask it to "complete"/"improve"/"repeat" the report (that made the supervisor re-delegate and regenerate report after report); the first `writer` is enough, then close with `finalize`.
3. **Per-worker cap** (`MAX_WORKER_STEPS = 4`) — each worker cuts its own loop.

As a bonus: the `provider.py` adapter provides **multi-provider failover** (gemini → groq → openrouter) **with anti-quota backoff** (see below). If all providers fail at once, the worker reports it to the supervisor (which decides with what it has) instead of crashing the graph.

#### Anti-quota backoff in `provider.py` (shared)

`provider.chat()` retries the provider chain on **transient** failures (429 quota/rate-limit, 503, "retry in Xs", Groq's intermittent 400 `failed to render... harmony`): if ALL failed with transient errors, it waits **5s → 15s → 40s** and retries up to 3 rounds. **Permanent** errors (bad API key, bad schema) abort at once, with no waiting. This keeps a quota spike from killing a long run (a worker mid-loop, or a supervisor deciding the next step) — which was exactly the "it did not work: all providers failed at once" symptom producing an empty report.

### Clear logs: what each agent did

Each agent prints with its own label, so you can "watch the team think":

```
  [Supervisor] Delegaciones usadas: 0/6
  [Supervisor] Decide → delegar a 'data': Necesito los datos de ventas del Q1...
  [Ruteador] state['decisions'][-1] = data → worker_data
    [Worker data] Recibe orden del supervisor: ...
      [data·paso 1] Turno del modelo (proveedor: groq) → herramienta(s): query_sales
      [data] ejecuté query_sales({'segment': 'Enero'}) → Registros: 9 | ...
  [Supervisor] Decide → delegar a 'analyst': Con el siguiente JSON de ventas del Q1...
    [Worker analyst] ...
  [Supervisor] Decide → delegar a 'writer': ...
  [Supervisor] Decide → delegar a 'finalize': Informe de ventas Q1 completado.
```

### Embedded dataset (rich, for many questions)

`sales.py` `SALES` is a simulated store of **6 months (Jan→Jun) × 8 products in 4 categories** = 54 deterministic records (month, category, product, units, amount). It has a **real trend**: Q1 flat-to-down in peripherals, Q2 with peaks in monitors and notebooks; audio is stable. That allows total, comparison, top-product, trend and anomaly questions.

Real example questions:
- *"Build a Q1 sales report: totals per month and category, the star product and how the trend evolved."*
- *"Compare both quarters (Q1 vs Q2). Which category grew the most? What was the semester's best month and the best-selling product in units?"*
- *"How did peripherals evolve month by month? Is there any worrying drop?"*
- *"Give me the best and worst month of the semester, with their totals."*

### Getting started

Requirement: at least one API key in the root `.env` (Gemini, Groq or OpenRouter — the failover chain uses whatever is configured).

```bash
venv\Scripts\python.exe 09-multi-agent-orchestration\multi_agent.py --info    # what the dataset contains
venv\Scripts\python.exe 09-multi-agent-orchestration\multi_agent.py --test    # automatic: 2 end-to-end requests
venv\Scripts\python.exe 09-multi-agent-orchestration\multi_agent.py           # interactive chat mode
```

### Questions to try

- How many agents are there and what does each do? → the `[Supervisor]`, `[Worker data]`, etc. prints.
- Does the supervisor delegate one step at a time or all at once? → the prints: first `data`, then `analyst`, then `writer`, then `finalize`.
- Does each worker have its own tools? → the writer does NOT query the store (no `query_sales`); data and analyst do.
- What if you ask for something that needs Q1 and Q2? → data queries separately and analyst compares.
- Is there an iteration limit? → `[Supervisor] Delegaciones usadas: N/6` counter; the graph cuts with what it has.
- What if all providers fail? → the worker reports the error to the supervisor, which closes with what it has (no graph crash).

### Files

```
09-multi-agent-orchestration/
├── multi_agent.py   # the multi-agent system: supervisor + 3 workers on LangGraph
├── provider.py      # multi-provider adapter with anti-quota backoff (shared)
└── README.md
```

### Typical errors

| Error / symptom | What it means | How to fix it |
|---|---|---|
| `RuntimeError: All providers failed (...)` inside a worker | Quota/network of ALL providers at once | `provider.chat` already backs off (retries 3× with 5s/15s/40s on transient failures). If it still fails, check `.env`: move the provider that is out of quota today to the end (e.g. `PROVIDERS=groq,openrouter,gemini`) |
| The supervisor repeats the same delegation | The model does not see progress in the state (or ignored the anti-loop rule) | Make sure the accumulated `results` are in `_context`; strengthen the rule in `SUPERVISOR_PROMPT` |
| `UnicodeEncodeError: 'charmap'` on console | Windows cp1252 does not understand `→`/emoji | Already solved with `sys.stdout.reconfigure(encoding="utf-8")` in `main()` |
| `GraphRecursionError` | `recursion_limit` reached | Adjust `RECURSION_LIMIT` (or lower `MAX_DELEGATIONS`) |
| The report "loses" a month | The supervisor context truncates long results to 4000 chars | Query the segment separately or raise the cap in `_context()` |

### Skills covered

- **Manager-workers pattern** on LangGraph: a coordinating supervisor + working workers
- **Hierarchical delegation**: one synthetic tool `delegate(worker, message)` as the supervisor's single output
- **Workers with scoped tools and prompts**, each with its own encapsulated ReAct loop
- **Triple anti-infinite-loop control**: global `recursion_limit` + delegation cap + per-worker internal cap
- **Multi-provider failover with anti-quota backoff** in the shared adapter
- **Architecture**: accumulated shared state (decisions + results) so the supervisor "sees" all progress